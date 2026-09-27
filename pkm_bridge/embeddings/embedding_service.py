"""Background embedding service for periodic note embedding."""

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import or_

from config.settings import Config
from pkm_bridge.database import Document, DocumentChunk, get_db
from pkm_bridge.embeddings.chunker import NoteChunker
from pkm_bridge.embeddings.voyage_client import VoyageClient
from pkm_bridge.note_paths import RG_NOTE_FILTER


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash for change detection."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_date_from_file(file_path: Path, logger=None) -> Optional[str]:
    """Extract date from filename, path, or file content.

    Tries multiple strategies in order:
    1. Journal directory path (e.g., /journals/2024-12-09.org or /journals/2024_12_09.md)
    2. Filename pattern (YYYY-MM-DD or YYYY_MM_DD)
    3. Org-mode #+title or property drawer
    4. File modification time as fallback

    Returns:
        Date string in YYYY-MM-DD format or None
    """
    import re

    def log(msg):
        if logger:
            logger.debug(msg)

    # Strategy 1: Extract from journal path
    # Handle both org-mode (/journals/YYYY-MM-DD.org) and Logseq (/journals/YYYY_MM_DD.md)
    if "/journals/" in str(file_path):
        # Try YYYY-MM-DD format
        date_match = re.search(r"/journals/(\d{4}-\d{2}-\d{2})", str(file_path))
        if date_match:
            log(f"Date from journal path: {date_match.group(1)}")
            return date_match.group(1)

        # Try YYYY_MM_DD format (Logseq)
        date_match = re.search(r"/journals/(\d{4}_\d{2}_\d{2})", str(file_path))
        if date_match:
            date_str = date_match.group(1).replace("_", "-")
            log(f"Date from Logseq journal path: {date_str}")
            return date_str

    # Strategy 2: Extract from filename
    # Try YYYY-MM-DD format
    filename_match = re.search(r"(\d{4}-\d{2}-\d{2})", file_path.name)
    if filename_match:
        log(f"Date from filename: {filename_match.group(1)}")
        return filename_match.group(1)

    # Try YYYY_MM_DD format (Logseq)
    filename_match = re.search(r"(\d{4}_\d{2}_\d{2})", file_path.name)
    if filename_match:
        date_str = filename_match.group(1).replace("_", "-")
        log(f"Date from Logseq filename: {date_str}")
        return date_str

    # Strategy 3: Extract from org-mode content
    if file_path.suffix == ".org":
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                in_properties = False
                for line in f:
                    line = line.strip()

                    # Check #+title
                    if line.startswith("#+title:"):
                        title = line.split(":", 1)[1].strip()
                        title_match = re.search(r"(\d{4}-\d{2}-\d{2})", title)
                        if title_match:
                            log(f"Date from #+title: {title_match.group(1)}")
                            return title_match.group(1)

                    # Check property drawer
                    if line == ":PROPERTIES:":
                        in_properties = True
                        continue
                    if line == ":END:":
                        in_properties = False
                        continue

                    if in_properties:
                        # Look for :DATE: or :CREATED: properties
                        if line.startswith(":DATE:") or line.startswith(":CREATED:"):
                            prop_value = line.split(":", 2)[2].strip()
                            prop_match = re.search(r"(\d{4}-\d{2}-\d{2})", prop_value)
                            if prop_match:
                                log(f"Date from property drawer: {prop_match.group(1)}")
                                return prop_match.group(1)

                    # Stop reading after first heading or non-header content
                    if (
                        not line.startswith("#")
                        and not line.startswith(":")
                        and not in_properties
                        and line
                    ):
                        break
        except Exception as e:
            log(f"Error reading file content: {e}")

    # Strategy 4: Use file modification time as fallback
    try:
        mtime = file_path.stat().st_mtime
        date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        log(f"Date from mtime fallback: {date_str}")
        return date_str
    except Exception as e:
        log(f"Error getting mtime: {e}")

    return None


def embed_document(
    file_path: Path,
    voyage_client: VoyageClient,
    chunker: NoteChunker,
    db,
    force: bool = False,
    logger=None,
) -> bool:
    """Embed a single document (all chunks).

    Args:
        file_path: Path to the note file
        voyage_client: Voyage AI client
        chunker: Note chunker
        db: Database session
        force: Force re-embedding even if unchanged
        logger: Optional logger

    Returns:
        True if embedded, False if skipped
    """

    def log(msg):
        if logger:
            logger.info(msg)
        else:
            print(msg)

    # Compute file hash
    try:
        current_hash = compute_file_hash(file_path)
    except Exception as e:
        log(f"❌ Error reading {file_path}: {e}")
        return False

    # Check if already embedded
    existing = db.query(Document).filter_by(file_path=str(file_path)).first()

    if existing and existing.file_hash == current_hash and not force:
        # Most files are unchanged on most runs; the caller logs a summary
        if logger:
            logger.debug(f"⏭️  Skip (unchanged): {file_path.name}")
        else:
            print(f"⏭️  Skip (unchanged): {file_path.name}")
        return False

    # Chunk the document
    try:
        chunks = chunker.chunk_file(file_path)
    except Exception as e:
        log(f"❌ Error chunking {file_path}: {e}")
        return False

    if not chunks:
        log(f"⚠️  No chunks created for {file_path.name}")
        # File shrank below the chunking threshold. Clear any stale chunks and
        # store the current hash so this file isn't reprocessed on every run.
        try:
            if existing:
                db.query(DocumentChunk).filter_by(document_id=existing.id).delete()
                doc = existing
                doc.file_hash = current_hash
                doc.total_chunks = 0
                doc.updated_at = datetime.utcnow()
                doc.last_embedded_at = datetime.utcnow()
            else:
                doc = Document(
                    file_path=str(file_path),
                    file_type=file_path.suffix[1:],
                    file_hash=current_hash,
                    date_extracted=extract_date_from_file(file_path, logger=logger),
                    total_chunks=0,
                    last_embedded_at=datetime.utcnow(),
                )
                db.add(doc)
            db.commit()
        except Exception as e:
            db.rollback()
            log(f"❌ Error recording empty-chunk state for {file_path}: {e}")
        return False

    # Batch embed all chunks
    try:
        texts = [chunk.content for chunk in chunks]
        result = voyage_client.embed(texts=texts, input_type="document")
        embeddings = result.embeddings
    except Exception as e:
        log(f"❌ Error embedding {file_path}: {e}")
        return False

    # Extract date
    date_extracted = extract_date_from_file(file_path, logger=logger)

    # Save to database
    try:
        if existing:
            # Delete old chunks
            db.query(DocumentChunk).filter_by(document_id=existing.id).delete()
            doc = existing
            doc.file_hash = current_hash
            doc.updated_at = datetime.utcnow()
            doc.date_extracted = date_extracted
        else:
            # Create new document
            doc = Document(
                file_path=str(file_path),
                file_type=file_path.suffix[1:],  # 'org' or 'md'
                file_hash=current_hash,
                date_extracted=date_extracted,
            )
            db.add(doc)
            db.flush()  # Get ID

        # Insert chunks
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_obj = DocumentChunk(
                document_id=doc.id,
                chunk_index=idx,
                chunk_type=chunk.chunk_type,
                heading_path=chunk.heading_path,
                content=chunk.content,
                start_line=chunk.start_line,
                token_count=chunk.token_count,
                embedding=embedding,
            )
            db.add(chunk_obj)

        doc.total_chunks = len(chunks)
        doc.last_embedded_at = datetime.utcnow()
        db.commit()

        total_tokens = sum(c.token_count for c in chunks)
        log(f"✅ Embedded: {file_path.name} ({len(chunks)} chunks, {total_tokens} tokens)")
        return True

    except Exception as e:
        db.rollback()
        log(f"❌ Error saving {file_path}: {e}")
        return False


def scan_note_files(directories: list[Path], logger=None) -> tuple[list[Path], list[Path]]:
    """Find all .org and .md files in directories using ripgrep.

    Uses ripgrep to find files, which automatically respects .gitignore
    and filters out backup directories, internal config, sync files, etc.

    Args:
        directories: List of directories to search
        logger: Optional logger

    Returns:
        (file paths sorted by modification time, newest first;
         the directories whose listing is known to be complete). A directory
        where rg hit an error (exit 2, e.g. one unreadable subdirectory) still
        contributes the files it did list, but is not complete, so callers
        must not treat files missing from it as deleted.
    """
    import subprocess

    def log(msg):
        if logger:
            logger.info(msg)
        else:
            print(msg)

    files = []
    complete_dirs = []

    for directory in directories:
        directory = Path(directory)
        if not directory.exists():
            log(f"⚠️  Directory not found: {directory}")
            continue

        cmd = ["rg", "--files", *RG_NOTE_FILTER, str(directory)]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
            )

            # 0 = listed files; 1 = found none; 2 = listed what it could
            if result.returncode in (0, 2):
                files.extend(Path(line) for line in result.stdout.splitlines() if line)
            if result.returncode == 0:
                complete_dirs.append(directory)
            elif result.returncode == 2:
                log(f"⚠️  ripgrep partially failed in {directory}: {result.stderr.strip()}")
            else:
                log(f"⚠️  ripgrep returned code {result.returncode} for {directory}")

        except subprocess.TimeoutExpired:
            log(f"⚠️  Timeout searching {directory}")
        except FileNotFoundError:
            log("⚠️  ripgrep not found. Please install ripgrep (rg)")
            # Fallback to glob (less reliable)
            for pattern in ["**/*.org", "**/*.md"]:
                files.extend(directory.glob(pattern))

    def mtime(f: Path) -> float:
        try:
            return f.stat().st_mtime
        except OSError:  # deleted since the scan
            return 0.0

    return sorted(files, key=mtime, reverse=True), complete_dirs


def find_note_files(directories: list[Path], logger=None) -> list[Path]:
    """All note files in directories, newest first (see scan_note_files)."""
    return scan_note_files(directories, logger)[0]


def embed_gmail_messages(
    voyage_client: VoyageClient,
    gmail_oauth,
    db,
    days_back: int = 7,
    max_emails: int = 200,
    logger=None,
) -> dict:
    """Embed recent Gmail messages into the RAG pipeline.

    Args:
        voyage_client: Voyage AI client
        gmail_oauth: GoogleOAuth instance for Gmail
        db: Database session
        days_back: How many days back to fetch emails
        max_emails: Maximum emails to process
        logger: Optional logger

    Returns:
        Dictionary with stats
    """
    from pkm_bridge.db_repository import OAuthRepository
    from pkm_bridge.google_gmail_client import GoogleGmailClient

    def log(msg):
        if logger:
            logger.info(msg)

    stats = {"gmail_embedded": 0, "gmail_skipped": 0, "gmail_errors": 0}

    if not gmail_oauth:
        return stats

    # Get Gmail token
    try:
        token = OAuthRepository.get_token(db, "google_gmail")
        if not token:
            log("Gmail not connected, skipping email embedding")
            return stats

        # Refresh if expired
        if OAuthRepository.is_token_expired(token):
            try:
                new_token_data = gmail_oauth.refresh_token(token.refresh_token)
                OAuthRepository.save_token(
                    db=db,
                    service="google_gmail",
                    access_token=new_token_data["access_token"],
                    refresh_token=new_token_data.get("refresh_token"),
                    expires_at=new_token_data["expires_at"],
                    scope=new_token_data.get("scope"),
                )
                token = OAuthRepository.get_token(db, "google_gmail")
            except Exception as e:
                log(f"Failed to refresh Gmail token for embedding: {e}")
                return stats

        client = GoogleGmailClient(token.access_token, token.refresh_token)
    except Exception as e:
        log(f"Error initializing Gmail client for embedding: {e}")
        return stats

    # Search for recent important emails only (Gmail's yellow flag)
    from datetime import datetime, timedelta

    date_str = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    query = f"is:important after:{date_str}"

    chunker = NoteChunker()

    try:
        all_messages = []
        page_token = None

        while len(all_messages) < max_emails:
            results = client.list_messages(
                query=query,
                max_results=min(50, max_emails - len(all_messages)),
                page_token=page_token,
            )
            messages = results.get("messages", [])
            if not messages:
                break
            all_messages.extend(messages)
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        log(f"📧 Found {len(all_messages)} recent emails to process for embedding")

        for msg_stub in all_messages:
            try:
                msg_id = msg_stub["id"]
                synthetic_path = f"gmail://{msg_id}"

                # Fetch full message
                msg = client.get_message(msg_id)
                payload = msg.get("payload", {})
                headers = payload.get("headers", [])

                subject = client.extract_header(headers, "Subject") or "(no subject)"
                from_addr = client.extract_header(headers, "From")
                date = client.extract_header(headers, "Date")
                body = client.decode_body(payload)

                # Compute hash for change detection
                content_hash = hashlib.sha256(
                    (subject + from_addr + body).encode("utf-8")
                ).hexdigest()

                # Check if already embedded
                existing = db.query(Document).filter_by(file_path=synthetic_path).first()
                if existing and existing.file_hash == content_hash:
                    stats["gmail_skipped"] += 1
                    continue

                # Chunk the email
                chunks = chunker.chunk_email(subject, from_addr, date, body)
                if not chunks:
                    stats["gmail_skipped"] += 1
                    continue

                # Embed chunks
                texts = [chunk.content for chunk in chunks]
                result = voyage_client.embed(texts=texts, input_type="document")
                embeddings = result.embeddings

                # Extract date for the document
                date_extracted = None
                try:
                    from email.utils import parsedate_to_datetime

                    dt = parsedate_to_datetime(date)
                    date_extracted = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

                # Save to database
                if existing:
                    db.query(DocumentChunk).filter_by(document_id=existing.id).delete()
                    doc = existing
                    doc.file_hash = content_hash
                    doc.updated_at = datetime.utcnow()
                    doc.date_extracted = date_extracted
                else:
                    doc = Document(
                        file_path=synthetic_path,
                        file_type="gmail",
                        file_hash=content_hash,
                        date_extracted=date_extracted,
                    )
                    db.add(doc)
                    db.flush()

                for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                    chunk_obj = DocumentChunk(
                        document_id=doc.id,
                        chunk_index=idx,
                        chunk_type=chunk.chunk_type,
                        heading_path=chunk.heading_path,
                        content=chunk.content,
                        start_line=chunk.start_line,
                        token_count=chunk.token_count,
                        embedding=embedding,
                    )
                    db.add(chunk_obj)

                doc.total_chunks = len(chunks)
                doc.last_embedded_at = datetime.utcnow()
                db.commit()

                stats["gmail_embedded"] += 1

            except Exception as e:
                db.rollback()
                if logger:
                    logger.error(f"Error embedding email {msg_stub.get('id')}: {e}")
                stats["gmail_errors"] += 1

    except Exception as e:
        log(f"Error fetching Gmail messages for embedding: {e}")

    log(
        f"📧 Gmail embedding complete: {stats['gmail_embedded']} embedded, "
        f"{stats['gmail_skipped']} skipped, {stats['gmail_errors']} errors"
    )
    return stats


def reconcile_deleted_files(
    files: list[Path], db, logger, scanned_dirs: list[Path] | None = None
) -> int:
    """Remove Document rows for filesystem files that no longer exist.

    Compares the current on-disk scan against `Document.file_path` and
    deletes any row (and its chunks, via cascade) whose path wasn't found.
    Only documents under `scanned_dirs` are considered, so a directory whose
    scan failed keeps its embeddings; paths from external sources (e.g.
    `gmail://...`, produced by `embed_gmail_messages`) are never touched.

    Args:
        files: Current on-disk note files (same set used for embedding)
        db: Database session
        logger: Logger instance
        scanned_dirs: Directories whose listing in `files` is complete;
            defaults to all filesystem-backed documents

    Returns:
        Number of documents removed
    """
    found_paths = {str(f) for f in files}

    try:
        query = db.query(Document).filter(~Document.file_path.startswith("gmail://"))
        if scanned_dirs is not None:
            if not scanned_dirs:
                return 0
            query = query.filter(
                or_(
                    *[Document.file_path.startswith(f"{d}/", autoescape=True) for d in scanned_dirs]
                )
            )
        stale_docs = query.filter(~Document.file_path.in_(found_paths)).all()

        for doc in stale_docs:
            logger.info(f"🗑️  Removing embeddings for missing file: {doc.file_path}")
            db.delete(doc)

        if stale_docs:
            db.commit()

        return len(stale_docs)

    except Exception as e:
        db.rollback()
        logger.error(f"Error reconciling deleted files: {e}")
        return 0


def run_incremental_embedding(
    logger, voyage_client: VoyageClient, config: Config = None, gmail_oauth=None
) -> dict:
    """Run incremental embedding (only changed files).

    This is the main entry point for the background scheduler.

    Args:
        logger: Logger instance
        voyage_client: Voyage AI client
        config: Optional config (will create if not provided)
        gmail_oauth: Optional GoogleOAuth instance for Gmail embedding

    Returns:
        Dictionary with stats (embedded_count, skipped_count, error_count)
    """
    if config is None:
        config = Config()

    logger.info("🔄 Starting incremental embedding...")

    # Get directories
    directories = [config.org_dir]
    if config.logseq_dir:
        directories.append(config.logseq_dir)

    # Find files
    files, complete_dirs = scan_note_files(directories, logger)

    if not files:
        logger.warning("No files found to embed")
    else:
        logger.info(f"📁 Found {len(files)} files to process")

    # Initialize components
    db = get_db()
    chunker = NoteChunker()

    # Process files
    embedded_count = 0
    skipped_count = 0
    error_count = 0
    deleted_count = 0

    try:
        for file_path in files:
            try:
                if embed_document(
                    file_path, voyage_client, chunker, db, force=False, logger=logger
                ):
                    embedded_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")
                error_count += 1

        # Reconcile deleted/renamed files, only within directories that were
        # listed completely: a failed or empty scan is more likely transient
        # than a genuinely emptied PKM, and must not wipe its embeddings.
        skipped_dirs = [d for d in directories if Path(d) not in complete_dirs]
        if skipped_dirs:
            logger.warning(
                "Skipping deletion reconciliation for incompletely scanned: "
                + ", ".join(str(d) for d in skipped_dirs)
            )
        deleted_count = reconcile_deleted_files(files, db, logger, scanned_dirs=complete_dirs)

        # Embed recent Gmail messages (if connected)
        if gmail_oauth:
            gmail_stats = embed_gmail_messages(voyage_client, gmail_oauth, db, logger=logger)
            embedded_count += gmail_stats.get("gmail_embedded", 0)
            skipped_count += gmail_stats.get("gmail_skipped", 0)
            error_count += gmail_stats.get("gmail_errors", 0)
    finally:
        db.close()

    logger.info(
        f"✅ Incremental embedding complete: {embedded_count} embedded, "
        f"{skipped_count} skipped, {deleted_count} deleted, {error_count} errors"
    )

    return {
        "embedded_count": embedded_count,
        "skipped_count": skipped_count,
        "deleted_count": deleted_count,
        "error_count": error_count,
    }
