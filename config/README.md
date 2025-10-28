# Configuration Files

## system_prompt.txt
Main system prompt for Claude. This is the generic prompt that's safe to commit to version control.

## user_context.txt (gitignored)
Personal information about you (profession, interests, family, etc.). This helps Claude understand your context when working with your notes.

**Setup:**
```bash
cp user_context.txt.example user_context.txt
nano user_context.txt  # Add your personal info
chmod 600 user_context.txt  # Keep it private
```

This file is gitignored to protect your privacy when pushing to GitHub.

## user_context.txt.example
Template showing what to include in user_context.txt.

## settings.py
Python configuration loader that:
- Loads environment variables from .env (or .env.local)
- Loads and merges system_prompt.txt + user_context.txt
- Provides configuration to the Flask app
