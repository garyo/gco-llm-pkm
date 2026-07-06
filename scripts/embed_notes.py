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

This is a thin CLI wrapper: the actual embedding logic (hashing, chunking,
date extraction, file discovery) lives in
pkm_bridge.embeddings.embedding_service, which is shared with the background
scheduler, so this script and the scheduler can't diverge.
"""

import argparse
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from config.settings import Config
from pkm_bridge.database import Document, DocumentChunk, get_db, init_db
from pkm_bridge.embeddings.chunker import NoteChunker
from pkm_bridge.embeddings.embedding_service import embed_document, find_note_files
from pkm_bridge.embeddings.voyage_client import VoyageClient


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
        print("📝 Loaded environment from .env")

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
