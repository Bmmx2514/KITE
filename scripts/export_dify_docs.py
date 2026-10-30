"""Export KITE fixture documents as uploadable Dify text files."""

import argparse
from pathlib import Path

from kite.lightrag import fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=fixtures()[1])
    parser.add_argument("--role", help="Export only documents visible to this role")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    docs, profiles = fixtures()
    selected = [docs[doc_id] for doc_id in profiles[args.profile]
                if args.role is None or args.role in docs[doc_id]["roles"]]
    if not selected:
        raise ValueError("no documents match the requested profile and role")
    args.out.mkdir(parents=True, exist_ok=False)
    for doc in selected:
        content = (f"Title: {doc['title']}\nVersion: {doc['version']}\n"
                   f"Valid from: {doc['valid_from']}\nValid to: {doc['valid_to'] or 'current'}\n\n"
                   f"{doc['content']}\n")
        (args.out / f"{doc['id']}.txt").write_text(content, encoding="utf-8")
    print(f"Exported {len(selected)} documents to {args.out}")


if __name__ == "__main__":
    main()
