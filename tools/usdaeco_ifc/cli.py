"""IFC conversion and session import commands."""
import argparse

def main(argv=None):
    parser = argparse.ArgumentParser(prog="aeco-ifc")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("convert", help="Convert an IFC to core USD", add_help=False)
    imp = sub.add_parser("init", help="Import kind drivers and create a sync session")
    imp.add_argument("ifc")
    imp.add_argument("model")
    imp.add_argument("--directory")
    args, rest = parser.parse_known_args(argv)
    if args.command == "convert":
        from .convert.cli import main as convert
        return convert(rest)
    if rest:
        parser.error("unrecognized arguments: " + " ".join(rest))
    from aeco_sync import register_plugins
    register_plugins()
    from .kind_import import main as initialize
    session = initialize(args.ifc, args.model, args.directory)
    print(session.path)
    return 0
