import { describe, expect, test } from 'bun:test';
import { EditorState, EditorSelection, type Transaction } from '@codemirror/state';
import { syntaxTree, ensureSyntaxTree } from '@codemirror/language';
import { markdown, markdownLanguage, insertNewlineContinueMarkup } from '@codemirror/lang-markdown';
import { yamlFrontmatter } from '@pkm/editor/md-frontmatter';

function mdState(doc: string, cursor = doc.length) {
  const state = EditorState.create({
    doc,
    selection: EditorSelection.cursor(cursor),
    extensions: markdown({ base: markdownLanguage, extensions: yamlFrontmatter }),
  });
  ensureSyntaxTree(state, state.doc.length, 5000);
  return state;
}

function topNodes(doc: string): string[] {
  const names: string[] = [];
  const cursor = syntaxTree(mdState(doc)).cursor();
  if (cursor.firstChild()) {
    do names.push(cursor.name);
    while (cursor.nextSibling());
  }
  return names;
}

describe('markdown front matter', () => {
  test('a leading YAML block is front matter, not a setext heading', () => {
    const nodes = topNodes('---\ndate: 2026-09-27\ntags: [journal]\n---\n\n# Today\n');
    expect(nodes[0]).toBe('Frontmatter');
    expect(nodes).not.toContain('SetextHeading2');
    expect(nodes).toContain('ATXHeading1');
  });

  test('a leading horizontal rule stays a rule', () => {
    expect(topNodes('---\n\nJust text\n')[0]).toBe('HorizontalRule');
  });

  test('--- later in the file is not front matter', () => {
    expect(topNodes('Intro\n\n---\ndate: x\n---\n')).not.toContain('Frontmatter');
  });
});

describe('markdown task lists', () => {
  test('Enter after a task item starts a new unchecked one', () => {
    let state = mdState('- [x] done thing');
    insertNewlineContinueMarkup({
      state,
      dispatch: (tr: Transaction) => {
        state = tr.state;
      },
    });
    expect(state.doc.toString()).toBe('- [x] done thing\n- [ ] ');
  });
});
