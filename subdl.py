#!/usr/bin/env python3

# subdl - command-line tool to download subtitles from opensubtitles.org.
#
# Uses code from subdownloader (a GUI app).

NAME = "subdl"
VERSION = "1.1.2"

VERSION_INFO = """\

This is free software; see the source for copying conditions.
There is NO warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

https://github.com/alexanderwink/subdl
"""

import os, sys
import struct
import xmlrpc.client
import io, gzip, base64
import re
import argparse
import json

OSDB_SERVER_URI = "https://api.opensubtitles.org/xml-rpc"
xmlrpc_server = None
login = None
osdb_token = None
options = None

BLACKLIST = [
    "opensubtitles",
    "addic7ed",
    "joycasino",
    "bitninja\.io",
    "Please rate this subtitle at www\.osdb\.link",
    "allsubs",
    "firebit\.org",
    "humanguardians\.com",
    "subtitles by",
    "recast\.ai",
    "by mstoll",
    "subs corrected",
    "by tronar",
    "titlovi",
    "^_$",
    "^- _$",
]


LANGUAGES = [
    ["alb", "sq", "Albanian"],
    ["ara", "ar", "Arabic"],
    ["arm", "hy", "Armenian"],
    ["ass", "ay", "Assyrian"],
    ["bos", "bs", "Bosnian"],
    ["pob", "pb", "Portuguese-BR"],
    ["bul", "bg", "Bulgarian"],
    ["cat", "ca", "Catalan"],
    ["chi", "zh", "Chinese"],
    ["hrv", "hr", "Croatian"],
    ["cze", "cs", "Czech"],
    ["dan", "da", "Danish"],
    ["dut", "nl", "Dutch"],
    ["eng", "en", "English"],
    ["est", "et", "Estonian"],
    ["fin", "fi", "Finnish"],
    ["fre", "fr", "French"],
    ["glg", "gl", "Galician"],
    ["geo", "ka", "Georgian"],
    ["ger", "de", "German"],
    ["ell", "gr", "Greek"],
    ["heb", "he", "Hebrew"],
    ["hin", "hi", "Hindi"],
    ["hun", "hu", "Hungarian"],
    ["ice", "is", "Icelandic"],
    ["ind", "id", "Indonesian"],
    ["ita", "it", "Italian"],
    ["jpn", "ja", "Japanese"],
    ["kaz", "kk", "Kazakh"],
    ["kor", "ko", "Korean"],
    ["lav", "lv", "Latvian"],
    ["lit", "lt", "Lithuanian"],
    ["ltz", "lb", "Luxembourgish"],
    ["mac", "mk", "Macedonian"],
    ["may", "ms", "Malay"],
    ["nor", "no", "Norwegian"],
    ["per", "fa", "Farsi"],
    ["pol", "pl", "Polish"],
    ["por", "pt", "Portuguese"],
    ["rum", "ro", "Romanian"],
    ["rus", "ru", "Russian"],
    ["scc", "sr", "Serbian"],
    ["slo", "sk", "Slovak"],
    ["slv", "sl", "Slovenian"],
    ["spa", "es", "Spanish"],
    ["swe", "sv", "Swedish"],
    ["tha", "th", "Thai"],
    ["tur", "tr", "Turkish"],
    ["ukr", "uk", "Ukrainian"],
    ["vie", "vi", "Vietnamese"],
]


def fatal_error(message, code=1):
    sys.stderr.write(f"Error: {message}\n")
    sys.exit(code)


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
        with open(filename, "wb") as f:
            f.write(str)
    except Exception as e:
        fatal_error("Error writing to %s: %s" % (filename, e))


def query_num(s, low, high):
    while True:
        try:
            n = input("%s [%d..%d] " % (s, low, high))
        except KeyboardInterrupt:
            fatal_error("Aborted by user")
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
            fatal_error("Aborted by user")
        if r.startswith("y"):
            return True
        elif r.startswith("n"):
            return False


def filtersub(s):
    s = s.strip()
    line_sep = b"\r\n" if re.search(b"\r\n", s) else b"\n"
    subs = re.split(b"(?:\r?\n){2,}", s)
    subs = [re.split(b"\r?\n", sub, 2) for sub in subs]
    filter_pattern = re.compile("|".join(BLACKLIST).encode(), re.M | re.I)
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
    longlongformat = "<Q"
    bytesize = struct.calcsize(longlongformat)
    assert bytesize == 8
    filesize = os.path.getsize(name)
    hash = filesize
    if filesize < 65536 * 2:
        raise Exception("Error hashing %s: file too small" % (name))
    with open(name, "rb") as f:
        for x in range(int(65536 / bytesize)):
            hash += struct.unpack(longlongformat, f.read(bytesize))[0]
            hash &= 0xFFFFFFFFFFFFFFFF
        f.seek(filesize - 65536, 0)
        for x in range(int(65536 / bytesize)):
            hash += struct.unpack(longlongformat, f.read(bytesize))[0]
            hash &= 0xFFFFFFFFFFFFFFFF
    return "%016x" % hash


def SearchSubtitlesByHash(filename, langs_search):
    moviehash = movie_hash(filename)
    moviebytesize = os.path.getsize(filename)
    searchlist = [
        (
            {
                "sublanguageid": langs_search,
                "moviehash": moviehash,
                "moviebytesize": str(moviebytesize),
            }
        )
    ]
    print("Searching for subtitles for moviehash=%s..." % (moviehash), file=sys.stderr)
    try:
        results = xmlrpc_server.SearchSubtitles(osdb_token, searchlist)
    except Exception as e:
        fatal_error("Error in XMLRPC SearchSubtitles call: %s" % e)
    data = results["data"]
    return data and [SubtitleSearchResult(d) for d in data]


def SearchSubtitlesByIMDBId(filename, langs_search, imdb_id):
    result = re.search("\d+", imdb_id)
    imdb_id = result.group(0)
    searchlist = [({"sublanguageid": langs_search, "imdbid": imdb_id})]
    print("Searching for subtitles for IMDB id=%s..." % (imdb_id), file=sys.stderr)
    try:
        results = xmlrpc_server.SearchSubtitles(osdb_token, searchlist)
    except Exception as e:
        fatal_error("Error in XMLRPC SearchSubtitles call: %s" % e)
    data = results["data"]
    return data and [SubtitleSearchResult(d) for d in data]


def SearchSubtitlesByString(str, langs_search):
    searchlist = [({"sublanguageid": langs_search, "query": str})]
    print("Searching for subtitles for query=%s..." % (str), file=sys.stderr)
    try:
        results = xmlrpc_server.SearchSubtitles(osdb_token, searchlist)
    except Exception as e:
        fatal_error("Error in XMLRPC SearchSubtitles call: %s" % e)
    data = results["data"]
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
        if options.download == "query":
            print("%s." % repr(n).rjust(count_maxlen), end=" ")
        print(
            "#%s [%s] [Rat:%s DL:%s] %s %s "
            % (
                idsubtitle.rjust(idsubtitle_maxlen),
                lang,
                rating.rjust(4),
                downloads.rjust(downloads_maxlen),
                moviename.ljust(moviename_maxlen),
                filename,
            )
        )


def DisplaySelectedSubtitle(selected_file):
    print("#{0.IDSubtitleFile} {0.SubFileName}".format(selected_file))


def DownloadSubtitle(sub_id):
    """Download subtitle #sub_id and return subtitle text as string."""
    try:
        answer = xmlrpc_server.DownloadSubtitles(osdb_token, [sub_id])
        if answer.get("data") == False:
            print("\n  ------\n  ERROR: ", answer.get("status"), "\n  ------\n")
            os._exit()
        else:
            subtitle_compressed = answer["data"][0]["data"]
    except Exception as e:
        fatal_error("Error in XMLRPC DownloadSubtitles call: %s" % e)
    return gunzipstr(base64.b64decode(subtitle_compressed))


def DownloadAndSaveSubtitle(sub_id, destfilename):
    if os.path.exists(destfilename):
        if options.existing == "abort":
            fatal_error(
                "Subtitle %s already exists; aborting (try --interactive)."
                % destfilename,
                code=3,
            )
        elif options.existing == "bypass":
            print("Subtitle %s already exists; bypassing." % destfilename)
            return
        elif options.existing == "overwrite":
            print("Subtitle %s already exists; overwriting." % destfilename)
        elif options.existing == "query":
            if query_yn("Subtitle %s already exists. Overwrite?" % destfilename):
                pass
            else:
                fatal_error("File not overwritten.")
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
            print(
                f"Found encoding {result['encoding']} with a confidence of {result['confidence']*100:.2f}%. Converting to utf8."
            )
            # separate lines for easier debugging
            s = s.decode(result["encoding"])  # bytes -> str
            s = s.encode("utf8")  # str -> bytes
    writefile(destfilename, s)
    print("done, wrote %d bytes." % (len(s)), file=sys.stderr)


def format_subtitle_output_filename(videoname, search_result):
    subname = search_result.SubFileName
    repl = {
        "I": search_result.IDSubtitleFile,
        "m": file_base(videoname),
        "M": file_ext(videoname),
        "s": file_base(subname),
        "S": file_ext(subname),
        "l": search_result.LanguageName,
        "L": search_result.ISO639,
    }
    output_filename = options.output.format(**repl)
    assert output_filename != videoname
    return output_filename


def AutoDownloadAndSave(videoname, search_result, downloaded=None):
    output_filename = format_subtitle_output_filename(videoname, search_result)
    if downloaded is not None:
        if output_filename in downloaded:
            fatal_error(
                "Already wrote to %s! Uniquify output filename format."
                % output_filename
            )
        downloaded[output_filename] = 1
    DownloadAndSaveSubtitle(search_result.IDSubtitleFile, output_filename)


def select_search_result_by_id(id, search_results):
    for search_result in search_results:
        if search_result.IDSubtitleFile == id:
            return search_result
    fatal_error("Search results did not contain subtitle with id %s" % id)


def help():
    print(__doc__)


def isnumber(value):
    try:
        return int(value) > 0
    except:
        return False


def ListLanguages():
    languages = xmlrpc_server.GetSubLanguages("")["data"]
    print("Available languages:")
    for language in languages:
        print(language["SubLanguageID"], language["ISO639"], language["LanguageName"])


def save_login(username='', password=''):
    file = os.path.join(os.getenv('XDG_CONFIG_HOME', os.getenv('HOME')),
                        'subdl.json')

    login = {'username': username, 'password': password}
    if username and password:
        with open(file, 'w') as f:
            json.dump(login, f, indent=2)
    elif os.path.isfile(file):
        with open(login, 'r') as f:
            return json.load(f).values()
    return login.values()


def osdb_connect():
    global xmlrpc_server, login, osdb_token

    username, password = save_login(options.osdb_username,
                                    options.osdb_password)

    xmlrpc_server = xmlrpc.client.ServerProxy(OSDB_SERVER_URI)
    login = xmlrpc_server.LogIn(
        username, password, "en", NAME + " " + VERSION
    )
    if login["status"] != "200 OK":
        fatal_error("Failed connecting to opensubtitles.org: " + login["status"])
    osdb_token = login["token"]


def parseargs(args):
    parser = argparse.ArgumentParser(
        description="Subdl - command-line tool to download subtitles from opensubtitles.org",
        add_help=True,
        epilog=VERSION_INFO,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    accepted_languages = []
    for langs in LANGUAGES:
        accepted_languages += langs

    parser.add_argument("--version", help="Print version and exit", action="store_true")
    parser.add_argument(
        "--versionx", help="Print version only and exit", action="store_true"
    )
    parser.add_argument(
        "--list-languages", help="List languages and exit", action="store_true"
    )
    parser.add_argument(
        "--username",
        dest="osdb_username",
        help="OSDB username",
        default="",
        metavar="USER",
    )
    parser.add_argument(
        "--password",
        dest="osdb_password",
        help="OSDB password",
        default="",
        metavar="PASS",
    )
    parser.add_argument("--search", help="Search for subtitles")
    parser.add_argument("--imdb-id", help="IMDB ID", metavar="ID")
    parser.add_argument("--force-imdb", help="Force IMDB ID", action="store_true")
    parser.add_argument("--output", help="Output filename format")
    choices=["abort", "bypass", "overwrite", "query"]
    parser.add_argument(
        "--existing",
        help=f"Action to take if subtitle already exists.\nValues: {', '.join(choices)}",
        choices=choices,
        default="abort",
        metavar="ACTION",
    )
    parser.add_argument("--interactive", help="Interactive mode", action="store_true")
    parser.add_argument("--utf8", help="Convert subtitles to utf8", action="store_true")
    parser.add_argument(
        "--filter", help="Filter subtitles for text", action="store_true"
    )
    parser.add_argument(
        "--download", help="Download subtitles", default="first", metavar="SPEC"
    )
    parser.add_argument(
        "--lang",
        help="Subtitle language. Values: See --list-languages",
        choices=accepted_languages,
        default="eng",
        metavar="LANG",
    )
    parser.add_argument(
        "-n", help="Display search results and exit", action="store_true"
    )
    parser.add_argument("--force-filename", help="Force filename", action="store_true")
    parser.add_argument("files", nargs="*", help="Video files")

    parser.add_argument("--path", required=False, default="results")

    options = parser.parse_args()

    if options.versionx:
        print(VERSION)
        raise SystemExit

    if options.version:
        print("%s %s" % (NAME, VERSION))
        raise SystemExit

    if options.list_languages:
        # FIXME AttributeError: 'NoneType' object has no attribute 'GetSubLanguages'
        #ListLanguages()
        for langs in LANGUAGES:
            print(" ".join(langs))
        raise SystemExit

    if options.n or options.download == "none":
        options.download = "none"

    if options.interactive:
        options.download = "query"
        options.existing = "query"

    if (options.download
        not in [
            "all",
            "first",
            "query",
            "none",
            "best-rating",
            "most-downloaded",
        ] and not isnumber(options.download)
    ):
        fatal_error(
            "Argument to --download must be numeric subtitle id or one: all, first, query, none"
        )

    if not options.output:
        options.output = default_output_fmt(options)

    if options.utf8:
        try:
            import chardet
        except ModuleNotFoundError:
            fatal_error(
                "The --utf8 option requires the chardet module from https://pypi.org/project/chardet/ - Hint: pip install chardet"
            )

    if len(options.files) == 0:
        fatal_error("The following arguments are required: files")

    if len(options.files) > 1 and options.force_imdb:
        fatal_error("Can't use --force-imdb with multiple files.")

    if len(options.files) > 1 and isnumber(options.download):
        fatal_error("Can't use --download=ID with multiple files.")

    return options


def default_output_fmt(options):
    if options.download == "all":
        return "{m}.{L}.{I}.{S}"
    elif options.lang == "all" or "," in options.lang:
        return "{m}.{L}.{S}"
    else:
        return "{m}.{S}"


def main(args):

    global options

    options = parseargs(args)

    osdb_connect()

    no_search_results = 0
    for file in options.files:
        selected_file = ""
        file_name = file_base(os.path.basename(file))

        if not os.path.exists(file):
            fatal_error("can't find file '%s'" % file)

        if options.search:
            search_results = SearchSubtitlesByString(options.search, options.lang)
        elif options.force_imdb:
            if options.imdb_id is None:
                fatal_error("With --force-imdb a --imdb-id must be provided.")
            search_results = SearchSubtitlesByIMDBId(
                file, options.lang, options.imdb_id
            )
        elif options.force_filename:
            search_results = SearchSubtitlesByString(file_name, options.lang)
        else:
            search_results = SearchSubtitlesByHash(file, options.lang)
            if not search_results and options.imdb_id is not None:
                print("No results found by hash, trying IMDB id")
                search_results = SearchSubtitlesByIMDBId(
                    file, options.lang, options.imdb_id
                )
            elif not search_results:
                print("No results found by hash, trying filename")
                search_results = SearchSubtitlesByString(file_name, options.lang)
        if not search_results:
            print("No results found.", file=sys.stderr)
            no_search_results = no_search_results + 1
            continue

        DisplaySubtitleSearchResults(search_results, file)
        if options.download == "none":
            # TODO verify
            raise SystemExit
        elif options.download == "first":
            selected_file = search_results[0]
            print()
            print("Defaulting to first result (try --interactive):")
            DisplaySelectedSubtitle(selected_file)
            print()
            AutoDownloadAndSave(file, search_results[0])
        elif options.download == "all":
            downloaded = {}
            for search_result in search_results:
                AutoDownloadAndSave(file, search_result, downloaded)
        elif options.download == "query":
            n = query_num("Enter result to download:", 1, len(search_results))
            AutoDownloadAndSave(file, search_results[n - 1])
        elif options.download == "best-rating":
            selected_file = max(search_results, key=lambda sub: float(sub.SubRating))
            print()
            print("Downloading subtitle with best rating:")
            DisplaySelectedSubtitle(selected_file)
            print()
            AutoDownloadAndSave(file, selected_file)
        elif options.download == "most-downloaded":
            selected_file = max(
                search_results, key=lambda sub: int(sub.SubDownloadsCnt)
            )
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
        fatal_error("Some subtitles were not found.")


def cli():
    main(sys.argv[1:])


if __name__ == "__main__":
    cli()
