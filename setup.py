from setuptools import setup


def readme():
    with open('README.md') as f:
        return f.read()


setup(
    name='subdl',
    version='1.1.2',
    description='Command-line tool to download subtitles from opensubtitles.org',
    long_description=readme(),
    author='Alexander Winkler',
    url='https://github.com/alexanderwink/subdl',
    entry_points={
        'console_scripts': [
            'subdl = subdl:cli'
        ]
    },
    packages=[
        ''
    ],
    license='GPLv3+'
)
