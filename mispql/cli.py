import argparse

from mispql import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='mispql',
        description='Query MISP in MispQL, an SQL-like language, over its REST API.',
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.parse_args(argv)
    return 0
