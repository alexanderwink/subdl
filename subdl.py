#!/usr/bin/env python3

# subdl - command-line tool to download subtitles from opensubtitles.org.
#
# Uses code from subdownloader (a GUI app).

__doc__ = '''\
Syntax: subdl [options] moviefile.avi ...

Subdl is a command-line tool for downloading subtitles from opensubtitles.org.

By default, it will search for English subtitles, display the results,
download the highest-rated result in the requested language and save it to the
appropriate filename.

Options:
  --help                     This text
  --version                  Print version and exit
  --lang=LANGUAGES           Comma-separated list of languages in 3-letter code, e.g.
                             'eng,spa,fre', or 'all' for all.  Default is 'eng'.
  --list-languages           List available languages and exit.
  --username                 opensubtitles.org username
  --password                 opensubtitles.org password
  --search                   Use search string to look for subtitles.
  --download=ID              Download a particular subtitle by numeric ID.
  --download=first           Download the first search result [default].
  --download=all             Download all search results.
  --download=best-rating     Download the result with best rating.
  --download=most-downloaded Download the most downloaded result.
  --download=query           Query which search result to download.
  --download=none, -n        Display search results and exit.
  --output=OUTPUT            Output to specified output filename.  Can include the
                             following format specifiers:
                             {I} subtitle id
                             {m} movie file base     {M} movie file extension
                             {s} subtitle file base  {S} subtitle file extension
                             {l} language (English)  {L} language (2-letter ISO639)
                             Default is "{m}.{S}"; if multiple languages are searched,
                             then the default is "{m}.{L}.{S}"; if --download=all, then
                             the default is "{m}.{L}.{I}.{S}".
  --existing=abort           Abort if output filename already exists [default].
  --existing=bypass          Exit gracefully if output filename already exists.
  --existing=overwrite       Overwrite if output filename already exists.
  --existing=query           Query whether to overwrite.
  --imdb-id=id               Query by IMDB id. Hash is tried first unless --force-imdb
                             is used. IMDB URLs are also accepted.
  --force-imdb               Force IMDB search. --imdb-id must be specified.
  --force-filename           Force search using filename.
  --filter                   Filter blacklisted texts from subtitle.
  --interactive, -i          Equivalent to --download=query --existing=query.
  --utf8                     Convert output to UTF-8 encoding (Unicode).
  '''

NAME = 'subdl'
VERSION = '1.1.2'

VERSION_INFO = '''\

This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

http://code.google.com/p/subdl/'''

import os, sys
import struct
import xmlrpc.client
import io, gzip, base64
import getopt
import re

OSDB_SERVER_URI = "https://api.opensubtitles.org/xml-rpc"
xmlrpc_server = None
login = None
osdb_token = None

BLACKLIST = [
    'opensubtitles',
    'addic7ed',
    'joycasino',
    'bitninja\.io',
    'Please rate this subtitle at www\.osdb\.link',
    'allsubs',
    'firebit\.org',
    'humanguardians\.com',
    'subtitles by',
    'recast\.ai',
    'by mstoll',
    'subs corrected',
    'by tronar',
    'titlovi',
    '^_$',
    '^- _$',
]

class Options: pass
options = Options()
options.lang = 'eng'
options.download = 'first'
options.output = None
options.existing = 'abort'
options.imdb_id = None
options.force_imdb = False
options.force_filename = False
options.filter = False
options.osdb_username = ''
options.osdb_password = ''
options.search = ''
options.utf8 = False

class SubtitleSearchResult:
    def __init__(self, dict):
        self.__dict__ = dict

def file_ext(filename):
    return os.path.splitext(filename)[1][1:]

def file_base(filename):
    return os.path.splitext(filename)[0]

def gunzipstr(zs):
    with gzip.open(io.BytesIO(zs)) as gz:
        return gz.read()

def writefile(filename, str):
    try:
        with open(filename, 'wb') as f:
            f.write(str)
    except Exception as e:
        raise SystemExit("Error writing to %s: %s" % (filename, e))

def query_num(s, low, high):
    while True:
        try:
            n = input("%s [%d..%d] " % (s, low, high))
        except KeyboardInterrupt:
            raise SystemExit("Aborted")
        try:
            n = int(n)
            if low <= n <= high:
                return n
        except:
            pass

def query_yn(s):
    while True:
        try:
            r = input("%s [y/n] " % s).lower()
        except KeyboardInterrupt:
            raise SystemExit("Aborted")
        if r.startswith('y'):
            return True
        elif r.startswith('n'):
            return False

def filtersub(s):
    s = s.strip()
    line_sep = b'\r\n' if re.search(b'\r\n', s) else b'\n'
    subs = re.split(b'(?:\r?\n){2,}', s)
    subs = [re.split(b'\r?\n', sub, 2) for sub in subs]
    filter_pattern = re.compile('|'.join(BLACKLIST).encode(), re.M | re.I)
    for i in range(len(subs) - 1, -1, -1):
        if len(subs[i]) < 3:
            del subs[i]
            continue
        text = subs[i][2]
        if filter_pattern.search(text):
            print("Removed", i + 1, ":", text)
            del subs[i]
    for i in range(len(subs)):
        subs[i][0] = str(i + 1).encode()
    subs = map(line_sep.join, subs)
    return (line_sep * 2).join(subs)

def movie_hash(name):
    longlongformat = '<Q'
    bytesize = struct.calcsize(longlongformat)
    assert bytesize == 8
    filesize = os.path.getsize(name)
    hash = filesize
    if filesize < 65536 * 2:
        raise Exception("Error hashing %s: file too small" % (name))
    with open(name, "rb") as f:
        for x in range(int(65536/bytesize)):
            hash += struct.unpack(longlongformat, f.read(bytesize))[0]
            hash &= 0xFFFFFFFFFFFFFFFF
        f.seek(filesize-65536, 0)
        for x in range(int(65536/bytesize)):
            hash += struct.unpack(longlongformat, f.read(bytesize))[0]
            hash &= 0xFFFFFFFFFFFFFFFF
    return "%016x" % hash

def SearchSubtitlesByHash(filename, langs_search):
    moviehash = movie_hash(filename)
    moviebytesize = os.path.getsize(filename)
    searchlist = [({'sublanguageid': langs_search,
                    'moviehash': moviehash,
                    'moviebytesize': str(moviebytesize)})]
    print("Searching for subtitles for moviehash=%s..." % (moviehash), file=sys.stderr)
    try:
        results = xmlrpc_server.SearchSubtitles(osdb_token, searchlist)
    except Exception as e:
        raise SystemExit("Error in XMLRPC SearchSubtitles call: %s" % e)
    data = results['data']
    return data and [SubtitleSearchResult(d) for d in data]

def SearchSubtitlesByIMDBId(filename, langs_search, imdb_id):
    result = re.search("\d+", imdb_id)
    imdb_id = result.group(0)
    searchlist = [({'sublanguageid': langs_search,
                    'imdbid': imdb_id})]
    print("Searching for subtitles for IMDB id=%s..." % (imdb_id), file=sys.stderr)
    try:
        results = xmlrpc_server.SearchSubtitles(osdb_token, searchlist)
    except Exception as e:
        raise SystemExit("Error in XMLRPC SearchSubtitles call: %s" % e)
    data = results['data']
    return data and [SubtitleSearchResult(d) for d in data]

def SearchSubtitlesByString(str, langs_search):
    searchlist = [({'sublanguageid': langs_search,
                    'query': str})]
    print("Searching for subtitles for query=%s..." % (str), file=sys.stderr)
    try:
        results = xmlrpc_server.SearchSubtitles(osdb_token, searchlist)
    except Exception as e:
        raise SystemExit("Error in XMLRPC SearchSubtitles call: %s" % e)
    data = results['data']
    return data and [SubtitleSearchResult(d) for d in data]

def format_movie_name(s):
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    s = s.replace('"', "'")
    return '"%s"' % s

def DisplaySubtitleSearchResults(search_results, file):
    print("Found %d results for '%s':" % (len(search_results), file))
    idsubtitle_maxlen = 0
    moviename_maxlen = 0
    downloads_maxlen = 0
    for subtitle in search_results:
        idsubtitle = subtitle.IDSubtitleFile
        idsubtitle_maxlen = max(idsubtitle_maxlen, len(idsubtitle))
        moviename = format_movie_name(subtitle.MovieName)
        moviename_maxlen = max(moviename_maxlen, len(moviename))
        downloads = subtitle.SubDownloadsCnt
        downloads_maxlen = max(downloads_maxlen, len(downloads))

    n = 0
    count_maxlen = len(repr(len(search_results)))
    for subtitle in search_results:
        n += 1
        idsubtitle = subtitle.IDSubtitleFile
        lang = subtitle.ISO639
        # langn = subtitle.LanguageName
        # str_uploader = subtitle.UserNickName or "Anonymous"
        moviename = format_movie_name(subtitle.MovieName)
        filename = subtitle.SubFileName
        rating = subtitle.SubRating
        downloads = subtitle.SubDownloadsCnt
        # idmovie = subtitle.IDMovie
        # idmovieimdb = subtitle.IDMovieImdb
        if options.download == 'query':
            print("%s." % repr(n).rjust(count_maxlen), end=" ")
        print("#%s [%s] [Rat:%s DL:%s] %s %s " % (idsubtitle.rjust(idsubtitle_maxlen),
                                                lang,
                                                rating.rjust(4),
                                                downloads.rjust(downloads_maxlen),
                                                moviename.ljust(moviename_maxlen),
                                                filename))

def DisplaySelectedSubtitle(selected_file):
    print("#{0.IDSubtitleFile} {0.SubFileName}".format(selected_file))

def DownloadSubtitle(sub_id):
    '''Download subtitle #sub_id and return subtitle text as string.'''
    try:
        answer = xmlrpc_server.DownloadSubtitles(osdb_token, [sub_id])
        if answer.get('data') == False:
                print("\n  ------\n  ERROR: ",answer.get('status'),"\n  ------\n")
                os._exit()
        else:
                subtitle_compressed = answer['data'][0]['data']
    except Exception as e:
        raise SystemExit("Error in XMLRPC DownloadSubtitles call: %s" % e)
    return gunzipstr(base64.b64decode(subtitle_compressed))

def DownloadAndSaveSubtitle(sub_id, destfilename):
    if os.path.exists(destfilename):
        if options.existing == 'abort':
            print("Subtitle %s already exists; aborting (try --interactive)." % destfilename)
            raise SystemExit(3)
        elif options.existing == 'bypass':
            print("Subtitle %s already exists; bypassing." % destfilename)
            return
        elif options.existing == 'overwrite':
            print("Subtitle %s already exists; overwriting." % destfilename)
        elif options.existing == 'query':
            if query_yn("Subtitle %s already exists. Overwrite?" % destfilename):
                pass
            else:
                raise SystemExit("File not overwritten.")
        else:
            raise Exception("internal error: bad option.existing=%s" % options.existing)
    print("Downloading #%s to %s..." % (sub_id, destfilename), file=sys.stderr, end=" ")
    s = DownloadSubtitle(sub_id)
    if options.filter:
        s = filtersub(s)
    if options.utf8:
        import chardet
        result = chardet.detect(s)
        if not result["encoding"] in {"ascii", "utf-8"}:
            print(f"Found encoding {result['encoding']} with a confidence of {result['confidence']*100:.2f}%. Converting to utf8.")
            # separate lines for easier debugging
            s = s.decode(result["encoding"]) # bytes -> str
            s = s.encode("utf8") # str -> bytes
    writefile(destfilename, s)
    print("done, wrote %d bytes."% (len(s)), file=sys.stderr)

def format_subtitle_output_filename(videoname, search_result):
    subname = search_result.SubFileName
    repl = {
        'I': search_result.IDSubtitleFile,
        'm': file_base(videoname), 'M': file_ext(videoname),
        's': file_base(subname),   'S': file_ext(subname),
        'l': search_result.LanguageName,
        'L': search_result.ISO639
        }
    output_filename = options.output.format(**repl)
    assert output_filename != videoname
    return output_filename

def AutoDownloadAndSave(videoname, search_result, downloaded=None):
    output_filename = format_subtitle_output_filename(videoname, search_result)
    if downloaded is not None:
        if output_filename in downloaded:
            raise SystemExit("Already wrote to %s! Uniquify output filename format." % output_filename)
        downloaded[output_filename] = 1
    DownloadAndSaveSubtitle(search_result.IDSubtitleFile, output_filename)

def select_search_result_by_id(id, search_results):
    for search_result in search_results:
        if search_result.IDSubtitleFile == id:
            return search_result
    raise SystemExit("Search results did not contain subtitle with id %s" % id)

def help():
    print(__doc__)
    raise SystemExit

def isnumber(value):
    try:
        return int(value) > 0
    except:
        return False

def ListLanguages():
    languages = xmlrpc_server.GetSubLanguages('')['data']
    for language in languages:
        print(language['SubLanguageID'], language['ISO639'], language['LanguageName'])
    raise SystemExit

def default_output_fmt():
    if options.download == 'all':
        return "{m}.{L}.{I}.{S}"
    elif options.lang == 'all' or ',' in options.lang:
        return "{m}.{L}.{S}"
    else:
        return "{m}.{S}"


def osdb_connect():
    global xmlrpc_server, login, osdb_token
    xmlrpc_server = xmlrpc.client.ServerProxy(OSDB_SERVER_URI)
    login = xmlrpc_server.LogIn(options.osdb_username, options.osdb_password, "en", NAME+" "+VERSION)
    if login['status'] != '200 OK':
        raise SystemExit("Failed connecting to opensubtitles.org: " + login['status'])
    osdb_token = login["token"]


def parseargs(args):
    try:
        opts, arguments = getopt.getopt(args, 'h?in', [
                'existing=', 'lang=', 'search-only=',
                'download=', 'output=', 'interactive', 'utf8',
                'list-languages', 'imdb-id=', 'force-imdb',
                'force-filename', 'filter', 'help',
                'version', 'versionx', 'username=', 'password=',
                'search='])
    except getopt.GetoptError as e:
        raise SystemExit("%s: %s (see --help)" % (sys.argv[0], e))
    for option, value in opts:
        if option == '--help' or option == '-h' or option == '-?':
            help()
        elif option == '--versionx':
            print(VERSION)
            raise SystemExit
        elif option == '--version':
            print("%s %s" % (NAME, VERSION))
            raise SystemExit
        elif option == '--existing':
            if value in ['abort', 'overwrite', 'bypass', 'query']:
                pass
            else:
                raise SystemExit("Argument to --existing must be one of: abort, overwrite, bypass, query")
            options.existing = value
        elif option == '--lang':
            options.lang = value
        elif option == '--download':
            if value in ['all', 'first', 'query', 'none', 'best-rating', 'most-downloaded'] or isnumber(value):
                pass
            else:
                raise SystemExit("Argument to --download must be numeric subtitle id or one: all, first, query, none")
            options.download = value
        elif option == "--username":
            options.osdb_username = value
        elif option == "--password":
            options.osdb_password = value
        elif option == '--search':
            options.search = value
        elif option == '-n':
            options.download = 'none'
        elif option == '--output':
            options.output = value
        elif option == '--imdb-id':
            options.imdb_id = value
        elif option == '--force-imdb':
            options.force_imdb = True
        elif option == '--force-filename':
            options.force_filename = True
        elif option == '--filter':
            options.filter = True
        elif option == '--interactive' or option == '-i':
            options.download = 'query'
            options.existing = 'query'
        elif option == '--utf8':
            options.utf8 = True
            try:
                import chardet
            except ModuleNotFoundError:
                sys.stderr.write("Error: The --utf8 option requires the chardet module from https://pypi.org/project/chardet/ - Hint: pip install chardet\n")
                sys.exit(1)
        elif option == '--list-languages':
            ListLanguages()
        else:
            raise SystemExit("internal error: bad option '%s'" % option)
    if not options.output:
        options.output = default_output_fmt()
    if len(arguments) == 0:
        raise SystemExit("syntax: %s [options] filename.avi (see --help)" % (sys.argv[0]))
    if len(arguments) > 1 and options.force_imdb:
        raise SystemExit("Can't use --force-imdb with multiple files.")
    if len(arguments) > 1 and isnumber(options.download):
        raise SystemExit("Can't use --download=ID with multiple files.")

    return arguments

def main(args):
    files = parseargs(args)
    osdb_connect()

    no_search_results = 0
    for file in files:
        selected_file = '';
        file_name = file_base(os.path.basename(file))

        if not os.path.exists(file):
            raise SystemExit("can't find file '%s'" % file)

        if options.search:
            search_results = SearchSubtitlesByString(options.search, options.lang)
        elif options.force_imdb:
            if options.imdb_id is None:
                raise SystemExit("With --force-imdb a --imdb-id must be provided.")
            search_results = SearchSubtitlesByIMDBId(file, options.lang, options.imdb_id)
        elif options.force_filename:
            search_results = SearchSubtitlesByString(file_name, options.lang)
        else:
            search_results = SearchSubtitlesByHash(file, options.lang)
            if not search_results and options.imdb_id is not None:
                print("No results found by hash, trying IMDB id")
                search_results = SearchSubtitlesByIMDBId(file, options.lang, options.imdb_id)
            elif not search_results:
                print("No results found by hash, trying filename")
                search_results = SearchSubtitlesByString(file_name, options.lang)
        if not search_results:
            print("No results found.", file=sys.stderr)
            no_search_results = no_search_results + 1
            continue

        DisplaySubtitleSearchResults(search_results, file)
        if options.download == 'none':
            raise SystemExit
        elif options.download == 'first':
            selected_file = search_results[0]
            print()
            print("Defaulting to first result (try --interactive):")
            DisplaySelectedSubtitle(selected_file)
            print()
            AutoDownloadAndSave(file, search_results[0])
        elif options.download == 'all':
            downloaded = {}
            for search_result in search_results:
                AutoDownloadAndSave(file, search_result, downloaded)
        elif options.download == 'query':
            n = query_num("Enter result to download:",
                          1, len(search_results))
            AutoDownloadAndSave(file, search_results[n-1])
        elif options.download == 'best-rating':
            selected_file = max(search_results, key=lambda sub: float(sub.SubRating))
            print()
            print("Downloading subtitle with best rating:")
            DisplaySelectedSubtitle(selected_file)
            print()
            AutoDownloadAndSave(file, selected_file)
        elif options.download == 'most-downloaded':
            selected_file = max(search_results, key=lambda sub: int(sub.SubDownloadsCnt))
            print()
            print("Downloading most downloaded subtitle:")
            DisplaySelectedSubtitle(selected_file)
            print()
            AutoDownloadAndSave(file, selected_file)
        elif isnumber(options.download):
            search_result = select_search_result_by_id(options.download, search_results)
            AutoDownloadAndSave(file, search_result)
        else:
            raise Exception("internal error: bad option.download=%s" % options.download)

    if no_search_results > 0:
        raise SystemExit("One or more subtitles were not found.")


def cli():
    main(sys.argv[1:])

if __name__ == "__main__":
    cli()
