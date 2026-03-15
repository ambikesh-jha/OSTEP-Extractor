"""
services/merger.py
Merges all downloaded OSTEP chapter PDFs into one combined PDF
in the canonical book reading order.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.config import settings

log = logging.getLogger(__name__)

# ── Canonical chapter order ─────────────────────────────────────────
# Each tuple: (display_title, bare_filename_without_hash_prefix)

CHAPTER_ORDER: list[tuple[str, str]] = [
    # Intro
    ("Dedication",                          "dedication.pdf"),
    ("Preface",                             "preface.pdf"),
    ("1 Dialogue",                          "dialogue-threeeasy.pdf"),
    ("2 Introduction",                      "intro.pdf"),
    # Virtualization
    ("3 Dialogue",                          "dialogue-virtualization.pdf"),
    ("4 Processes",                         "cpu-intro.pdf"),
    ("5 Process API",                       "cpu-api.pdf"),
    ("6 Direct Execution",                  "cpu-mechanisms.pdf"),
    ("7 CPU Scheduling",                    "cpu-sched.pdf"),
    ("8 Multi-level Feedback",              "cpu-sched-mlfq.pdf"),
    ("9 Lottery Scheduling",               "cpu-sched-lottery.pdf"),
    ("10 Multi-CPU Scheduling",             "cpu-sched-multi.pdf"),
    ("11 Summary (CPU)",                    "cpu-dialogue.pdf"),
    ("12 Dialogue (VM)",                    "dialogue-vm.pdf"),
    ("13 Address Spaces",                   "vm-intro.pdf"),
    ("14 Memory API",                       "vm-api.pdf"),
    ("15 Address Translation",              "vm-mechanism.pdf"),
    ("16 Segmentation",                     "vm-segmentation.pdf"),
    ("17 Free Space Management",            "vm-freespace.pdf"),
    ("18 Introduction to Paging",           "vm-paging.pdf"),
    ("19 Translation Lookaside Buffers",    "vm-tlbs.pdf"),
    ("20 Advanced Page Tables",             "vm-smalltables.pdf"),
    ("21 Swapping: Mechanisms",             "vm-beyondphys.pdf"),
    ("22 Swapping: Policies",               "vm-beyondphys-policy.pdf"),
    ("23 Complete VM Systems",              "vm-complete.pdf"),
    ("24 Summary (VM)",                     "vm-dialogue.pdf"),
    # Concurrency
    ("25 Dialogue",                         "dialogue-concurrency.pdf"),
    ("26 Concurrency and Threads",          "threads-intro.pdf"),
    ("27 Thread API",                       "threads-api.pdf"),
    ("28 Locks",                            "threads-locks.pdf"),
    ("29 Locked Data Structures",           "threads-locks-usage.pdf"),
    ("30 Condition Variables",              "threads-cv.pdf"),
    ("31 Semaphores",                       "threads-sema.pdf"),
    ("32 Concurrency Bugs",                 "threads-bugs.pdf"),
    ("33 Event-based Concurrency",          "threads-events.pdf"),
    ("34 Summary (Concurrency)",            "threads-dialogue.pdf"),
    # Persistence
    ("35 Dialogue",                         "dialogue-persistence.pdf"),
    ("36 I/O Devices",                      "file-devices.pdf"),
    ("37 Hard Disk Drives",                 "file-disks.pdf"),
    ("38 Redundant Disk Arrays (RAID)",     "file-raid.pdf"),
    ("39 Files and Directories",            "file-intro.pdf"),
    ("40 File System Implementation",       "file-implementation.pdf"),
    ("41 Fast File System (FFS)",           "file-ffs.pdf"),
    ("42 FSCK and Journaling",              "file-journaling.pdf"),
    ("43 Log-structured File System (LFS)", "file-lfs.pdf"),
    ("44 Flash-based SSDs",                 "file-ssd.pdf"),
    ("45 Data Integrity and Protection",    "file-integrity.pdf"),
    ("46 Summary (Persistence)",            "file-dialogue.pdf"),
    ("47 Dialogue (Distribution)",          "dialogue-distribution.pdf"),
    ("48 Distributed Systems",              "dist-intro.pdf"),
    ("49 Network File System (NFS)",        "dist-nfs.pdf"),
    ("50 Andrew File System (AFS)",         "dist-afs.pdf"),
    ("51 Summary (Distributed)",            "dist-dialogue.pdf"),
    # Security
    ("52 Dialogue",                         "dialogue-security.pdf"),
    ("53 Intro Security",                   "security-intro.pdf"),
    ("54 Authentication",                   "security-authentication.pdf"),
    ("55 Access Control",                   "security-access.pdf"),
    ("56 Cryptography",                     "security-crypto.pdf"),
    ("57 Distributed (Security)",           "security-distributed.pdf"),
    # Appendices
    ("Dialogue (Virtual Machines)",         "dialogue-vmm.pdf"),
    ("Virtual Machines",                    "vmm-intro.pdf"),
    ("Dialogue (Monitors)",                 "dialogue-monitors.pdf"),
    ("Monitors",                            "threads-monitors.pdf"),
    ("Dialogue (Labs)",                     "dialogue-labs.pdf"),
    ("Lab Tutorial",                        "lab-tutorial.pdf"),
    ("Systems Labs",                        "lab-projects-systems.pdf"),
    ("xv6 Labs",                            "lab-projects-xv6.pdf"),
]


def _build_lookup(pdf_dir: Path) -> dict[str, Path]:
    """
    Strip the 6-char hash prefix the scraper adds to filenames.
    e.g. "3f8a1c_cpu-intro.pdf" → key "cpu-intro.pdf"
    """
    lookup: dict[str, Path] = {}
    for path in pdf_dir.glob("*.pdf"):
        parts = path.name.split("_", 1)
        bare  = parts[1] if len(parts) == 2 and len(parts[0]) == 6 else path.name
        lookup[bare] = path
    return lookup


class PDFMerger:
    """
    Merges downloaded chapter PDFs into a single combined PDF.

    Pass ``store`` to get per-chapter live WS progress updates.
    """

    def __init__(self, include_extras: bool = True, store=None) -> None:
        self.include_extras = include_extras
        self._store = store

    def _push(self, **fields) -> None:
        if self._store is not None:
            try:
                self._store.update(**fields)
            except Exception as exc:
                log.debug("store.update failed (non-fatal): %s", exc)

    def run(self) -> dict:
        try:
            from pypdf import PdfWriter, PdfReader
        except ImportError:
            raise RuntimeError("pypdf is not installed. Run: pip install pypdf")

        pdf_dir = settings.pdfs_dir
        if not pdf_dir.exists():
            raise RuntimeError(
                f"PDF directory not found: {pdf_dir}. "
                "Run the scraper first."
            )

        lookup  = _build_lookup(pdf_dir)
        writer  = PdfWriter()
        missing: list[str] = []
        included: list[str] = []
        total_pages = 0

        log.info("Found %d PDFs. Merging in book order …", len(lookup))

        for title, filename in CHAPTER_ORDER:
            if filename not in lookup:
                log.warning("MISSING  %-45s (%s)", title, filename)
                missing.append(filename)
                continue
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(lookup[filename]))
                pages  = len(reader.pages)
                writer.append(reader)
                total_pages += pages
                included.append(filename)
                log.info("✓  %-45s  %3d pp", title, pages)
                # Push live progress after each chapter
                if self.stats["chapters_fetched"] % 5 == 0:
                    self._push(
                        chapters_merged=len(included),
                        total_pages=total_pages,
                    )
            except Exception as exc:
                log.error("Error reading %s: %s", filename, exc)
                missing.append(filename)

        # Append unlisted extras
        if self.include_extras:
            known  = {fn for _, fn in CHAPTER_ORDER}
            extras = sorted(set(lookup.keys()) - known)
            for bare in extras:
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(str(lookup[bare]))
                    pages  = len(reader.pages)
                    writer.append(reader)
                    total_pages += pages
                    log.info("+ (extra)  %s  (%d pages)", bare, pages)
                    if self.stats["chapters_fetched"] % 5 == 0:
                        self._push(chapters_merged=len(included), total_pages=total_pages)
                except Exception as exc:
                    log.error("Error reading extra %s: %s", bare, exc)

        if total_pages == 0:
            raise RuntimeError("No pages were added — aborting merge.")

        output = settings.pdf_output
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "wb") as fh:
            writer.write(fh)

        size_mb = output.stat().st_size / (1024 * 1024)

        log.info("Merge complete — %d chapters, %d pages, %.1f MB",
                 len(included), total_pages, size_mb)

        return {
            "chapters_merged": len(included),
            "total_pages":     total_pages,
            "file_size_mb":    round(size_mb, 2),
            "output_file":     str(output),
            "missing_files":   missing,
        }
