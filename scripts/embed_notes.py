#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "sqlalchemy>=2.0.23",
#   "psycopg2-binary>=2.9.9",
#   "pgvector>=0.2.0",
#   "voyageai>=0.2.0",
#   "python-dotenv>=1.0.0",
#   "pyyaml>=6.0.2",
# ]
# ///
"""Batch embed all notes for RAG semantic search.

Usage:
    ./scripts/embed_notes.py                    # Embed all notes
    ./scripts/embed_notes.py --incremental      # Only changed files
    ./scripts/embed_notes.py --file path.org    # Single file
    ./scripts/embed_notes.py --limit 10         # Limit to N files (for testing)
    ./scripts/embed_notes.py --clear            # Clear all embeddings (with confirmation)
    ./scripts/embed_notes.py --clear --force    # Clear all embeddings (no confirmation)
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from pkm_bridge.database import get_db, Document, DocumentChunk, init_db
from pkm_bridge.embeddings.chunker import NoteChunker
from pkm_bridge.embeddings.voyage_client import VoyageClient
from config.settings import Config


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash for change detection."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        # Read in chunks for memory efficiency
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
                    # Only check first few lines
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
    force: bool = False
) -> bool:
    """Embed a single document (all chunks).

    Args:
        file_path: Path to the note file
        voyage_client: Voyage AI client
        chunker: Note chunker
        db: Database session
        force: Force re-embedding even if unchanged

    Returns:
        True if embedded, False if skipped
    """
    # Compute file hash
    try:
        current_hash = compute_file_hash(file_path)
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False

    # Check if already embedded
    existing = db.query(Document).filter_by(file_path=str(file_path)).first()

    if existing and existing.file_hash == current_hash and not force:
        print(f"⏭️  Skip (unchanged): {file_path.name}")
        return False

    # Chunk the document
    try:
        chunks = chunker.chunk_file(file_path)
    except Exception as e:
        print(f"❌ Error chunking {file_path}: {e}")
        return False

    if not chunks:
        print(f"⚠️  No chunks created for {file_path.name}")
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
        print(f"❌ Error embedding {file_path}: {e}")
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
        print(f"✅ Embedded: {file_path.name} ({len(chunks)} chunks, {total_tokens} tokens)")
        return True

    except Exception as e:
        db.rollback()
        print(f"❌ Error saving {file_path}: {e}")
        return False


def find_note_files(directories: List[Path]) -> List[Path]:
    """Find all .org and .md files in directories using ripgrep.

    Uses ripgrep to find files, which automatically respects .gitignore
    and filters out backup directories, internal config, sync files, etc.
    This ensures consistency with the search tools.

    Args:
        directories: List of directories to search

    Returns:
        List of file paths sorted by modification time (newest first)
    """
    import subprocess

    files = []

    for directory in directories:
        if not directory.exists():
            print(f"⚠️  Directory not found: {directory}")
            continue

        # Use ripgrep to list files (respects .gitignore automatically)
        # This is the same approach used by search_notes and find_context tools
        cmd = [
            'rg',
            '--files',  # List files only
            '--type-add', 'notes:*.{org,md}',  # Define custom type for notes
            '--type', 'notes',  # Only list note files
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
                # Parse file paths from output
                for line in result.stdout.strip().split('\n'):
                    if line:
                        files.append(Path(line))
            else:
                print(f"⚠️  ripgrep returned code {result.returncode} for {directory}")

        except subprocess.TimeoutExpired:
            print(f"⚠️  Timeout searching {directory}")
        except FileNotFoundError:
            print(f"⚠️  ripgrep not found. Please install ripgrep (rg)")
            # Fallback to glob (less reliable)
            for pattern in ['**/*.org', '**/*.md']:
                files.extend(directory.glob(pattern))

    # Sort by modification time (newest first)
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Embed notes for RAG semantic search")
    parser.add_argument('--file', type=Path, help="Embed a single file")
    parser.add_argument('--incremental', action='store_true', help="Only embed changed files")
    parser.add_argument('--force', action='store_true', help="Force re-embedding all files")
    parser.add_argument('--clear', action='store_true', help="Clear all embeddings from database")
    parser.add_argument('--limit', type=int, help="Limit number of files (for testing)")
    parser.add_argument('--org-dir', type=Path, help="Override ORG_DIR")
    parser.add_argument('--logseq-dir', type=Path, help="Override LOGSEQ_DIR")

    args = parser.parse_args()

    # Load environment - prefer .env.local for dev overrides
    env_local = Path(__file__).parent.parent / '.env.local'
    if env_local.exists():
        load_dotenv(env_local)
        print(f"📝 Loaded environment from {env_local}")
    else:
        load_dotenv()
        print(f"📝 Loaded environment from .env")

    # Get Voyage API key
    voyage_api_key = os.getenv('VOYAGE_API_KEY')
    if not voyage_api_key:
        print("❌ Error: VOYAGE_API_KEY not set in environment")
        print("   Get your API key from https://www.voyageai.com/")
        print("   Then add it to .env: VOYAGE_API_KEY=pa-your-key-here")
        sys.exit(1)

    # Initialize config
    config = Config()

    # Initialize database
    init_db()
    db = get_db()

    # Handle --clear flag (clear all embeddings)
    if args.clear:
        print("\n⚠️  WARNING: This will delete ALL embeddings from the database!")
        print("   You will need to re-embed all notes from scratch.")

        # Ask for confirmation unless --force is also specified
        if not args.force:
            response = input("\nAre you sure? Type 'yes' to confirm: ")
            if response.lower() != 'yes':
                print("❌ Aborted.")
                db.close()
                sys.exit(0)

        try:
            # Count existing data
            doc_count = db.query(Document).count()
            chunk_count = db.query(DocumentChunk).count()

            print(f"\n🗑️  Deleting {doc_count} documents and {chunk_count} chunks...")

            # Delete all chunks and documents (cascade will handle chunks)
            db.query(Document).delete()
            db.commit()

            print("✅ Database cleared successfully!")
            print("\nRun without --clear to re-embed your notes.")
            db.close()
            sys.exit(0)
        except Exception as e:
            db.rollback()
            print(f"❌ Error clearing database: {e}")
            db.close()
            sys.exit(1)

    # Get directories
    if args.file:
        files = [args.file]
    else:
        directories = []
        if args.org_dir:
            directories.append(args.org_dir)
        else:
            directories.append(config.org_dir)

        if args.logseq_dir:
            directories.append(args.logseq_dir)
        elif config.logseq_dir:
            directories.append(config.logseq_dir)

        files = find_note_files(directories)

        if args.limit:
            files = files[:args.limit]

    if not files:
        print("❌ No files found to embed")
        sys.exit(1)

    print(f"📁 Found {len(files)} files to process")

    # Initialize clients (db already initialized earlier)
    voyage_client = VoyageClient(api_key=voyage_api_key)
    chunker = NoteChunker()

    # Process files
    embedded_count = 0
    skipped_count = 0
    total_cost = 0.0

    try:
        for i, file_path in enumerate(files, 1):
            print(f"\n[{i}/{len(files)}] Processing: {file_path.name}")

            if embed_document(file_path, voyage_client, chunker, db, force=args.force):
                embedded_count += 1
            else:
                skipped_count += 1

    finally:
        db.close()

    # Summary
    print("\n" + "=" * 60)
    print(f"✅ Embedded: {embedded_count} files")
    print(f"⏭️  Skipped: {skipped_count} files")
    print("=" * 60)


if __name__ == "__main__":
    main()
