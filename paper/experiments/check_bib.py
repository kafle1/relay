#!/usr/bin/env python
"""Check every entry of a .bib file against Crossref.

For an entry with a DOI: fetch the record and compare title and year. For an entry without
one: search Crossref by title and report the best match so a DOI can be added or the entry
questioned. Nothing is rewritten; the output is a table to act on.

Usage:  python check_bib.py refs.bib [--fix-years]
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "bib-check (mailto:contact.me.kafle@gmail.com)"


def entries(path):
    text = open(path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, re.S):
        kind, key, body = m.group(1), m.group(2), m.group(3)
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)", body, re.S):
            v = fm.group(2).strip()
            if v[0] in "{\"":
                v = v[1:-1]
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", v.replace("{", "").replace("}", "")).strip()
        out.append((kind.lower(), key, fields))
    return out


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def similar(a, b):
    a, b = set(norm(a).split()), set(norm(b).split())
    return len(a & b) / max(1, len(a | b))


def main():
    path = sys.argv[1]
    bad = 0
    for kind, key, f in entries(path):
        title = f.get("title", "")
        year = f.get("year", "")
        doi = f.get("doi", "")
        if doi:
            try:
                m = get("https://api.crossref.org/works/" + urllib.parse.quote(doi))["message"]
            except Exception as e:
                print(f"FAIL  {key:<32} doi {doi} does not resolve ({e})"); bad += 1; continue
            ct = (m.get("title") or [""])[0]
            cy = (m.get("issued", {}).get("date-parts") or [[None]])[0][0]
            sim = similar(title, ct)
            flag = "ok  " if sim >= 0.6 and str(cy) == str(year) else "WARN"
            if flag == "WARN":
                bad += 1
            print(f"{flag}  {key:<32} sim={sim:.2f} year {year} vs {cy}  | crossref: {ct[:80]}")
        else:
            if kind in ("book", "misc", "manual", "techreport") and not f.get("journal"):
                print(f"skip  {key:<32} ({kind}, no doi expected)")
                continue
            q = urllib.parse.quote(title)
            try:
                items = get(f"https://api.crossref.org/works?rows=3&query.title={q}")["message"]["items"]
            except Exception as e:
                print(f"FAIL  {key:<32} search error {e}"); bad += 1; continue
            best = max(items, key=lambda i: similar(title, (i.get("title") or [""])[0]), default=None)
            if best is None:
                print(f"WARN  {key:<32} no crossref match"); bad += 1; continue
            bt = (best.get("title") or [""])[0]
            print(f"nodoi {key:<32} sim={similar(title, bt):.2f} -> {best.get('DOI')}  | {bt[:70]}")
        time.sleep(0.15)
    print(f"\n{bad} entries need attention")


if __name__ == "__main__":
    main()
