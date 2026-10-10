# Evolution mail extractor

Python 3.10+. Inventory and offline conversions use only the standard library; native Evolution preparation optionally uses system PyGObject/Camel bindings. Designed for offline recovery copies on Linux.

## Set up

Extract or clone the project, then open a terminal in its directory:

```bash
python3 -m venv .venv
```

There are no pip dependencies for inventory or offline conversion. On Ubuntu,
if creating the environment reports that venv is unavailable, install
`python3-venv` through your package manager and retry.

Work only from recovery copies made with the mail client closed. Keep a separate
verified backup. Conversion destinations must be new directories; failed output
must not be imported. Archive exports must remain unchanged for exact restoration.

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
| `.Sent.Household.Council` | Sent → Household → Council |
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

External import regenerates filenames and may normalize line endings/trailing separators through Python's mbox reader. It is **not byte-exact**. Mozilla read/replied/starred flags and conventional `Status: R` read flags are mapped. All external records, including records marked deleted, are recovered. External mbox folder names are encoded using Evolution’s `_2E` (dot) and `_5F` (underscore) rules, with a `..maildir++` marker. Inbox is mapped to the Maildir root; other folders use a single leading dot. Re-export retains that hierarchy. Folder mapping collisions stop the import instead of merging folders. Ordinary subdirectories are ignored; only `.sbd` hierarchies are scanned. `.msf`, `.json`, `msgFilterRules.dat` and `popstate.dat` are skipped.

## Scope and remaining integration work

- This is a converter for recovered files, not a live-profile migration utility.
- No account settings, passwords, contacts, filters, tags, spam scores, or client indexes are migrated.
- Generated mbox files add Mozilla read/replied/starred status headers when no existing `X-Mozilla-Status` is present. Existing Mozilla headers are retained and may take precedence over Maildir flags. Other Maildir flags survive an archival round trip through the manifest but are not mapped to Thunderbird UI state.
- Generic archival Maildir output is distinct from native Evolution preparation. Use `prepare-evolution` below to rebuild summaries and restore the supported flags before a manual installation into a closed profile.
- The user verified the corrected Thunderbird hierarchy and the standalone Camel restore with Evolution 3.56.2: 55,567 unchanged messages across 2,311 source folders, 55,547 known flag records and 5,641 Junk messages. The integrated command follows that tested process; validate its output in a separate directory before installation on another machine/version.
- Resource use includes one message at a time, manifest metadata and, in Junk-aware mode, the database snapshot and UID index. External mbox import also builds a mailbox index. There is no GUI; offline conversion uses only the standard library.

Thunderbird distinguishes raw message storage from `.msf` metadata, so file conversion cannot reproduce all client state: https://source-docs.thunderbird.net/en/latest/backend/folder_storage.html

Evolution folder-name reference: https://gnome.pages.gitlab.gnome.org/evolution-data-server/coverage/index.camel-maildir-store.c.f8f52b2bf0fe7b567e9c11b8c06c1531.html (maildir_full_name_to_dir_name / maildir_dir_name_to_fullname).


### Evolution Junk metadata

Use `to-mbox SOURCE NEW_DESTINATION --separate-junk` on an offline recovery copy
containing `folders.db`. Every message must match its original folder and UID in
the database, or have UID matches across message tables when its folder name differs.
A unique UID match is accepted; multiple matches are accepted only when every
full flag value agrees. All matching table names are recorded for consensus
matches, without claiming which table is the original folder. Both types are
counted separately. Conflicting flag values stop conversion; missing records, inconsistent Junk flags, pending journals, and
conflicting existing Mozilla status headers stop conversion. A failed output
retains CONVERSION-INCOMPLETE.txt and must not be imported. Retry with a new
destination after resolving the reported issue.

Junk messages move into ordinary `Junk-Recovery` subfolders retaining their
original hierarchy. They are not duplicated in the original exported folders.
This does not configure Thunderbird spam training or its native Junk score.
The summary counts Junk across all source folders, which may exceed Inbox-only
counts. Original message bytes and locations are retained in the version 2
manifest; the original database is stored in `evolution-metadata/folders.db`,
checksum protected, and restored by `to-maildir`. Old version 1 exports remain
supported. Use this updated converter for version 2 reverse conversion.

Actual read, replied, and important status comes from the Evolution flag bits,
not its derived `read` column. Deleted messages are retained visibly rather than
marked expunged in Thunderbird; their original flags remain in the manifest and
database. Metadata mode currently does not translate labels, follow-up tags,
or Thunderbird Junk training. The database is a snapshot of the original copy;
it is not rebuilt from edits made to the exported mbox files. Reverse conversion
is intended for an unchanged archive and verifies checksums.

When copying this export into Thunderbird Local Folders, exclude both
`conversion-manifest.json` and the `evolution-metadata` directory. Keep the
complete export elsewhere for reverse conversion. Original recovery, export,
and existing Thunderbird import are never edited by the converter.


If a recovery contains message files with no matching database UID, add
`--allow-missing-metadata` to `--separate-junk`. Those messages remain in their
original exported folders with filename-derived read/replied/star flags (existing
Mozilla status headers are retained). Their Junk status is unknown, not assumed
non-Junk. The summary reports their count; `missing-metadata.json` lists each
original folder, filename, and UID, and the manifest records the missing match.
Conflicting database flags still stop conversion. Exclude this JSON report when
copying the export into Thunderbird; retain it in the complete archival export.
Reverse conversion preserves these messages byte for byte too, but cannot
recover classifications missing from the source database.


## Prepare a native Evolution restore

Run with **system Python**, which can see installed PyGObject and Camel 1.2
introspection bindings. Existing inventory/conversion commands still work in
`.venv`. On the tested Ubuntu installation the required Camel package is
`gir1.2-camel-1.2`, alongside PyGObject (`python3-gi`). Other distributions package these bindings
differently; KDE is not required.

```bash
PYTHONPATH=src python3 -m evolution_mail_extractor prepare-evolution \
  "$HOME/Evolution-Mbox-Export-With-Junk" "$HOME/Evolution-Prepared"
```

The source must be an unchanged v2 export made with `--separate-junk`, containing
its original database and Evolution `..maildir++` marker. The destination must
be new, outside the live Evolution mail directory, and separate from the source.
No hardcoded user, UID, or message count is used. The command restores archived
bytes into `DESTINATION/mail/local`, adds missing standard folder structures,
lets Camel update summaries, and applies known read/replied/starred/Junk flags
through Camel. Each nonempty folder must expose exactly the archived UID set.
Saved SQLite flag records and every message checksum are checked afterward.
Folder-name aliases, unsupported native names and mismatches stop preparation.
The command does not automatically install a profile or transfer account settings.

Successful output contains `PREPARATION-VERIFIED.json`, the prepared `mail/local`
tree, and helper cache/certificate directories. Move only `mail/local` if manually
installing after review, with Evolution closed and the previous local tree saved.
Failure retains `PREPARATION-INCOMPLETE.txt`; never install failed output. Retry
with a new destination. Keep the original archive for exact archival restoration:
native preparation updates summaries and may rename message flag suffixes.

Messages with missing metadata remain accessible, without guessed Junk status;
Camel's rebuilt state is not a recovery of their unknown original flags. Deleted
flags are not applied, so recovered mail stays visible. Labels, follow-up tags,
forwarded flags and spam training are not translated. Empty standard folders may
be added. Native search-index warnings can occur; the final verification result
is required before installation.

Automated preparation tests use a fake Camel provider to exercise verification
failures and output handling. They do not replace a native application test.
The integrated CLI needs a sample run with the installed Camel version before
considering that release validated on the target machine.

## Release status and licence

Initial recovery utility: offline conversion has automated coverage and a reported
full-archive round trip. Native Evolution preparation is experimental until the
integrated command is tested with the target Camel version. This repository does
not include recovered emails, databases or conversion exports.

MIT licence; see [LICENSE](LICENSE).
