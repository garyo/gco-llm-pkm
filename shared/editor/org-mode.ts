import { StreamLanguage } from '@codemirror/language';
import { Tag, tags } from '@lezer/highlight';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';

/** Custom tags for Org mode syntax highlighting. */
export const orgTags = {
  propertyDrawer: Tag.define(tags.keyword),
  propertyKey: Tag.define(tags.propertyName),
  propertyValue: Tag.define(tags.string),
  blockDelimiter: Tag.define(tags.keyword),
  blockContent: Tag.define(tags.monospace),
  heading1: Tag.define(tags.heading1),
  heading2: Tag.define(tags.heading2),
  heading3: Tag.define(tags.heading3),
  headingTags: Tag.define(tags.meta),
  tableLine: Tag.define(tags.contentSeparator),
  checkboxUnchecked: Tag.define(tags.list),
  checkboxChecked: Tag.define(tags.list),
  checkboxCheckedText: Tag.define(tags.strikethrough),
  orgLink: Tag.define(),
};

/** Highlight styles for Org mode tokens. */
export const orgHighlightStyle = HighlightStyle.define([
  { tag: orgTags.propertyDrawer, color: '#61afef', fontWeight: 'bold', fontSize: '0.85em' },
  { tag: orgTags.propertyKey, color: '#e5c07b', fontWeight: '600', fontSize: '0.85em' },
  { tag: orgTags.propertyValue, color: '#98c379', fontSize: '0.85em' },
  { tag: orgTags.blockDelimiter, color: '#c678dd', fontWeight: 'bold' },
  { tag: orgTags.blockContent, color: '#abb2bf' },
  { tag: orgTags.heading1, color: '#e06c75', fontWeight: 'bold', fontSize: '1.4em' },
  { tag: orgTags.heading2, color: '#e06c75', fontWeight: 'bold', fontSize: '1.2em' },
  { tag: orgTags.heading3, color: '#e06c75', fontWeight: 'bold', fontSize: '1.05em' },
  { tag: tags.heading, color: '#e06c75', fontWeight: 'bold' },
  { tag: orgTags.headingTags, color: '#5c6370', fontStyle: 'italic' },
  { tag: orgTags.tableLine, color: '#61afef', backgroundColor: 'rgba(0, 0, 0, 0.3)' },
  { tag: orgTags.checkboxUnchecked, color: '#e5c07b', fontWeight: 'bold' },
  { tag: orgTags.checkboxChecked, color: '#7fad5f', fontWeight: 'bold' },
  { tag: orgTags.checkboxCheckedText, color: '#5c6370', textDecoration: 'line-through' },
  { tag: tags.strong, fontWeight: 'bold', color: '#d19a66' },
  { tag: tags.emphasis, fontStyle: 'italic', color: '#c678dd' },
  { tag: tags.monospace, fontFamily: 'monospace', color: '#98c379' },
  { tag: orgTags.orgLink, color: '#6cb6ff', textDecoration: 'underline', cursor: 'pointer' },
  { tag: tags.list, color: '#61afef' },
  { tag: tags.meta, color: '#5c6370', fontStyle: 'italic' },
]);

/** Blocks whose content is verbatim — no inline org markup applies inside. */
const VERBATIM_BLOCKS = new Set(['src', 'example', 'export']);

interface OrgState {
  /** Lowercase type of the enclosing #+begin_… block, or null outside blocks. */
  blockType: string | null;
  inPropertyDrawer: boolean;
  inCheckedItem: boolean;
  headingLevel: number;
}

/** StreamLanguage definition for org-mode syntax. */
export const orgMode = StreamLanguage.define<OrgState>({
  startState: () => ({
    blockType: null,
    inPropertyDrawer: false,
    inCheckedItem: false,
    headingLevel: 0,
  }),
  tokenTable: {
    'org-propertyDrawer': orgTags.propertyDrawer,
    'org-propertyKey': orgTags.propertyKey,
    'org-propertyValue': orgTags.propertyValue,
    'org-blockDelimiter': orgTags.blockDelimiter,
    'org-blockContent': orgTags.blockContent,
    'org-heading1': orgTags.heading1,
    'org-heading2': orgTags.heading2,
    'org-heading3': orgTags.heading3,
    'org-headingTags': orgTags.headingTags,
    'org-tableLine': orgTags.tableLine,
    'org-checkboxUnchecked': orgTags.checkboxUnchecked,
    'org-checkboxChecked': orgTags.checkboxChecked,
    'org-checkboxCheckedText': orgTags.checkboxCheckedText,
    'org-link': orgTags.orgLink,
  },
  token: (stream, state) => {
    if (stream.sol()) {
      state.inCheckedItem = false;
      state.headingLevel = 0;
    }

    // Table lines
    if (stream.sol() && stream.match(/^[\s]*\|.*\|[\s]*$/)) {
      return 'org-tableLine';
    }

    // Checked checkbox
    if (stream.sol() && stream.match(/^[\s]*-[\s]+\[[\s]*[Xx][\s]*\]/)) {
      state.inCheckedItem = true;
      return 'org-checkboxChecked';
    }

    // Unchecked checkbox
    if (stream.sol() && stream.match(/^[\s]*-[\s]+\[[\s]*\]/)) {
      return 'org-checkboxUnchecked';
    }

    // Strikethrough text after checked checkbox
    if (state.inCheckedItem && !stream.eol()) {
      stream.skipToEnd();
      return 'org-checkboxCheckedText';
    }

    // Property drawer start
    if (stream.sol() && stream.match(/^[\s]*:PROPERTIES:/i)) {
      state.inPropertyDrawer = true;
      return 'org-propertyDrawer';
    }

    // Property drawer end
    if (stream.sol() && stream.match(/^[\s]*:END:/i)) {
      state.inPropertyDrawer = false;
      return 'org-propertyDrawer';
    }

    // Inside property drawer
    if (state.inPropertyDrawer) {
      if (stream.sol() && stream.match(/^[\s]*:([A-Z_-]+):/)) {
        const value = stream.match(/[\s]*.*/) as RegExpMatchArray | false;
        if (value && value[0].trim()) {
          stream.backUp(value[0].length);
          return 'org-propertyKey';
        }
        return 'org-propertyKey';
      }
      if (!stream.sol() && stream.peek() !== ':') {
        stream.skipToEnd();
        return 'org-propertyValue';
      }
    }

    // Block delimiters (#+begin_quote, #+begin_src, …)
    if (stream.sol()) {
      const begin = stream.match(/^[\s]*#\+begin_([a-zA-Z-]+)/i) as RegExpMatchArray | false;
      if (begin) {
        state.blockType = begin[1].toLowerCase();
        stream.skipToEnd();
        return 'org-blockDelimiter';
      }
      if (stream.match(/^[\s]*#\+end_[a-zA-Z-]+/i)) {
        state.blockType = null;
        stream.skipToEnd();
        return 'org-blockDelimiter';
      }
    }

    // Verbatim block content; other block types keep normal inline markup
    if (state.blockType && VERBATIM_BLOCKS.has(state.blockType)) {
      stream.skipToEnd();
      return 'org-blockContent';
    }

    // Headings
    if (stream.sol()) {
      const match = stream.match(/^(\*+)\s/) as RegExpMatchArray | false;
      if (match) {
        const level = match[1].length;
        const restOfLine = stream.string.slice(stream.pos);
        const tagsMatch = restOfLine.match(
          /^(.*?)((?:\s+)?:[a-zA-Z0-9_@]+(?::[a-zA-Z0-9_@]+)*:)\s*$/
        );

        if (tagsMatch) {
          stream.pos += tagsMatch[1].length;
        } else {
          const linkIdx = restOfLine.indexOf('[[');
          if (linkIdx > 0) {
            stream.pos += linkIdx;
          } else if (linkIdx < 0) {
            stream.skipToEnd();
          }
        }

        state.headingLevel = level;
        if (level === 1) return 'org-heading1';
        if (level === 2) return 'org-heading2';
        if (level === 3) return 'org-heading3';
        return 'heading';
      }
    }

    // Tags at end of heading line
    if (stream.match(/(?:\s+)?:[a-zA-Z0-9_@]+(?::[a-zA-Z0-9_@]+)*:\s*$/)) {
      return 'org-headingTags';
    }

    // Bold: *text*, no whitespace just inside the stars
    if (stream.match(/\*(?:[^\s*][^*]*[^\s*]|[^\s*])\*/)) return 'strong';

    // Italic
    if (stream.match(/\/[^\/]+\//)) return 'emphasis';

    // Code
    if (stream.match(/[=~][^=~]+[=~]/)) return 'monospace';

    // Links [[target][description]]
    if (stream.match(/\[\[[^\]]+\]\[[^\]]+\]\]/)) return 'org-link';

    // Simple links [[target]]
    if (stream.match(/\[\[[^\]]+\]\]/)) return 'org-link';

    // Heading continuation
    if (state.headingLevel > 0) {
      const rest = stream.string.slice(stream.pos);
      const linkIdx = rest.indexOf('[[');
      if (linkIdx > 0) {
        stream.pos += linkIdx;
      } else {
        stream.skipToEnd();
      }
      if (state.headingLevel === 1) return 'org-heading1';
      if (state.headingLevel === 2) return 'org-heading2';
      if (state.headingLevel === 3) return 'org-heading3';
      return 'heading';
    }

    // List items
    if (stream.sol() && stream.match(/^[\s]*[-+]\s/)) return 'list';

    // Directives
    if (stream.sol() && stream.match(/^#\+.*/)) return 'meta';

    stream.next();
    return null;
  },
});

/** Convenience: combined syntaxHighlighting extension. */
export const orgHighlighting = syntaxHighlighting(orgHighlightStyle);
