"""Background embedding service for periodic note embedding."""

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pkm_bridge.database import get_db, Document, DocumentChunk
from pkm_bridge.embeddings.chunker import NoteChunker
from pkm_bridge.embeddings.voyage_client import VoyageClient
from config.settings import Config


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash for change detection."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_date_from_file(file_path: Path) -> Optional[str]:
    """Extract date from filename or file content.

    Returns:
        Date string in YYYY-MM-DD format or None
    """
    import re

    # Try to extract from filename (e.g., 2024-12-09.org)
    filename_match = re.search(r'(\d{4}-\d{2}-\d{2})', file_path.name)
    if filename_match:
        return filename_match.group(1)

    # Try to extract from org-mode #+title (for org files)
    if file_path.suffix == '.org':
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('#+title:'):
                        title = line.split(':', 1)[1].strip()
                        title_match = re.search(r'(\d{4}-\d{2}-\d{2})', title)
                        if title_match:
                            return title_match.group(1)
                    if not line.startswith('#'):
                        break
        except Exception:
            pass

    return None


def embed_document(
    file_path: Path,
    voyage_client: VoyageClient,
    chunker: NoteChunker,
    db,
    force: bool = False,
    logger=None
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
        log(f"⏭️  Skip (unchanged): {file_path.name}")
        return False

    # Chunk the document
    try:
        chunks = chunker.chunk_file(file_path)
    except Exception as e:
        log(f"❌ Error chunking {file_path}: {e}")
        return False

    if not chunks:
        log(f"⚠️  No chunks created for {file_path.name}")
        return False

    # Batch embed all chunks
    try:
        texts = [chunk.content for chunk in chunks]
        result = voyage_client.embed(
            texts=texts,
            input_type="document"
        )
        embeddings = result.embeddings
    except Exception as e:
        log(f"❌ Error embedding {file_path}: {e}")
        return False

    # Extract date
    date_extracted = extract_date_from_file(file_path)

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
                date_extracted=date_extracted
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
                embedding=embedding
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


def find_note_files(directories: list[Path], logger=None) -> list[Path]:
    """Find all .org and .md files in directories using ripgrep.

    Uses ripgrep to find files, which automatically respects .gitignore
    and filters out backup directories, internal config, sync files, etc.

    Args:
        directories: List of directories to search
        logger: Optional logger

    Returns:
        List of file paths sorted by modification time (newest first)
    """
    import subprocess

    def log(msg):
        if logger:
            logger.info(msg)
        else:
            print(msg)

    files = []

    for directory in directories:
        if not directory.exists():
            log(f"⚠️  Directory not found: {directory}")
            continue

        cmd = [
            'rg',
            '--files',
            '--type-add', 'notes:*.{org,md}',
            '--type', 'notes',
            str(directory)
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line:
                        files.append(Path(line))
            else:
                log(f"⚠️  ripgrep returned code {result.returncode} for {directory}")

        except subprocess.TimeoutExpired:
            log(f"⚠️  Timeout searching {directory}")
        except FileNotFoundError:
            log(f"⚠️  ripgrep not found. Please install ripgrep (rg)")
            # Fallback to glob (less reliable)
            for pattern in ['**/*.org', '**/*.md']:
                files.extend(directory.glob(pattern))

    # Sort by modification time (newest first)
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def run_incremental_embedding(logger, voyage_client: VoyageClient, config: Config = None) -> dict:
    """Run incremental embedding (only changed files).

    This is the main entry point for the background scheduler.

    Args:
        logger: Logger instance
        voyage_client: Voyage AI client
        config: Optional config (will create if not provided)

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
    files = find_note_files(directories, logger)

    if not files:
        logger.warning("No files found to embed")
        return {"embedded_count": 0, "skipped_count": 0, "error_count": 0}

    logger.info(f"📁 Found {len(files)} files to process")

    # Initialize components
    db = get_db()
    chunker = NoteChunker()

    # Process files
    embedded_count = 0
    skipped_count = 0
    error_count = 0

    try:
        for file_path in files:
            try:
                if embed_document(file_path, voyage_client, chunker, db, force=False, logger=logger):
                    embedded_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")
                error_count += 1
    finally:
        db.close()

    logger.info(f"✅ Embedding complete: {embedded_count} embedded, {skipped_count} skipped, {error_count} errors")

    return {
        "embedded_count": embedded_count,
        "skipped_count": skipped_count,
        "error_count": error_count
    }
