# Evolution mail extractor — corrected folder mapping

Python 3.10+; standard library only. Designed for offline recovery copies on Linux.

## Check the installation

From the project directory:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m evolution_mail_extractor --help
```

The original read-only inventory command still works:

```bash
PYTHONPATH=src .venv/bin/python -m evolution_mail_extractor "$HOME/Evolution-Recovery/local"
```

## Export Evolution Maildir folders to mbox

```bash
PYTHONPATH=src .venv/bin/python -m evolution_mail_extractor to-mbox \
  "$HOME/Evolution-Recovery/local" "$HOME/Evolution-Mbox-Export"
```

The destination must not exist. Each folder becomes an mboxrd file. Nested folders use Thunderbird's `.sbd` directory convention. Empty parent mailboxes are created where required.

Evolution names are interpreted using its local Maildir rules:

| Disk folder | Exported hierarchy |
| --- | --- |
| Archive root (`cur`, `new`, `tmp`) | Inbox |
| `.Sent` | Sent |
| `.Sent.38ANew.Council` | Sent → 38ANew → Council |
| `.Humans` | Humans |
| `..Personal.Shopping` | Inbox → Personal → Shopping |

Normal ASCII names remain readable. Percent encoding is no longer used: the first release's `%2ESent` filename produced a folder visible in Thunderbird with an empty message list even though five messages were present. The same file showed all five messages under a simple name. Names containing unsafe or unusual characters use reversible `~HH` UTF-8 byte escapes instead; literal tildes are escaped too. Some displayed names may therefore differ, but the manifest retains exact source names. Encoded special-character names still require an application-level sample test.

If the Evolution `..maildir++` marker exists, `_HH` literal-character escapes are decoded before generating storage names. The marker contents are retained for archival round-trip restoration. Without the marker, underscores are treated literally. Empty hierarchy components, such as a trailing dot in a legacy name, are preserved as `~empty` placeholder mailboxes. A literal folder named `~empty` is encoded separately as `~7Eempty`; folders are never silently merged. The original trailing-dot name is restored from the manifest. Naming collisions stop the export rather than merge folders.

This corrected release also places previously flattened `..` folders beneath Inbox. Create a fresh export and a fresh Thunderbird import; do not merge it into the first import.

Source messages are read but not changed. Symbolic links are refused. No deduplication: every message file is exported. Files outside `cur` and `new`, including Evolution `.cmeta` indexes, are not transferred.

## Restore an untouched export to Maildir

```bash
PYTHONPATH=src .venv/bin/python -m evolution_mail_extractor to-maildir \
  "$HOME/Evolution-Mbox-Export" "$HOME/Evolution-Maildir-Roundtrip"
```

Keep `conversion-manifest.json` with the export. It stores offsets, filenames and SHA-256 checksums. For an untouched export, restoration verifies every message and restores its original bytes, folder path, message filename, and Maildir flags, including `cur` versus `new`. Attachments remain inside the original MIME message.

Import an **independent copy** into Thunderbird. Do not open, compact or edit the archival export itself: changing the mbox invalidates manifest offsets/checksums. Failed conversions retain a `CONVERSION-INCOMPLETE.txt` marker and partial output; use a new destination for a retry. Source and destination cannot be nested or the same location.

## Import external mbox archives

For a single mbox file or a copied Thunderbird local-folder tree:

```bash
PYTHONPATH=src .venv/bin/python -m evolution_mail_extractor to-maildir \
  /path/to/copied-mbox-tree "$HOME/Imported-Maildir" --mbox-dialect mboxrd
```

Only choose `mboxrd` when the source's escaping convention is known. `mboxo` is also accepted; it has inherently ambiguous quoting of literal `>From ` lines. `mboxcl` and `mboxcl2` are unsupported. Automatic format detection is deliberately omitted because guessing can alter message bodies.

External import regenerates filenames and may normalize line endings/trailing separators through Python's mbox reader. It is **not byte-exact**. Mozilla read/replied/starred flags and conventional `Status: R` read flags are mapped. All external records, including records marked deleted, are recovered. External mbox folder names are encoded using Evolution’s `_2E` (dot) and `_5F` (underscore) rules, with a `..maildir++` marker. Inbox is mapped to the Maildir root; other folders use a single leading dot. Re-export retains that hierarchy. Ordinary subdirectories are ignored; only `.sbd` hierarchies are scanned. `.msf`, `.json`, `msgFilterRules.dat` and `popstate.dat` are skipped.

## Scope and remaining integration work

- This is a converter for recovered files, not a live-profile migration utility.
- No account settings, passwords, contacts, filters, tags, spam scores, or client indexes are migrated.
- Generated mbox files add Mozilla read/replied/starred status headers when no existing `X-Mozilla-Status` is present. Existing Mozilla headers are retained and may take precedence over Maildir flags. Other Maildir flags survive an archival round trip through the manifest but are not mapped to Thunderbird UI state.
- Generic Maildir output still requires an Evolution import workflow. It must not be copied into a running Evolution profile.
- The user verified ordinary-folder import and attachments in Thunderbird. The corrected Sent/Inbox hierarchy and special-character storage names still need an application-level test. Evolution application import has not yet been verified. Test a small sample in separate application profiles before importing the full archive.
- Resource use is approximately one message in memory, plus the manifest metadata. No GUI or third-party dependencies.

Thunderbird distinguishes raw message storage from `.msf` metadata, so file conversion cannot reproduce all client state: https://source-docs.thunderbird.net/en/latest/backend/folder_storage.html

Evolution folder-name reference: https://gnome.pages.gitlab.gnome.org/evolution-data-server/coverage/index.camel-maildir-store.c.f8f52b2bf0fe7b567e9c11b8c06c1531.html (maildir_full_name_to_dir_name / maildir_dir_name_to_fullname).
