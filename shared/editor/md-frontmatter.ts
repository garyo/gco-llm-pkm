import type { MarkdownConfig } from '@lezer/markdown';
import { tags } from '@lezer/highlight';

const FENCE_RE = /^(---|\.\.\.)\s*$/;
const YAML_KEY_RE = /^[\w-]+\s*:/;

/**
 * A YAML front-matter block (`---` / `key: value` lines / `---`) at the very
 * start of a markdown file. Without this the closing `---` turns the last
 * key into a setext heading.
 */
export const yamlFrontmatter: MarkdownConfig = {
  defineNodes: [{ name: 'Frontmatter', block: true, style: tags.meta }],
  parseBlock: [
    {
      name: 'Frontmatter',
      before: 'HorizontalRule',
      parse(cx, line) {
        // Require a key on the next line so a leading horizontal rule stays one.
        if (cx.lineStart !== 0 || line.text.trimEnd() !== '---' || !YAML_KEY_RE.test(cx.peekLine())) {
          return false;
        }
        while (cx.nextLine()) {
          if (FENCE_RE.test(line.text)) {
            const end = cx.lineStart + line.text.length;
            cx.nextLine();
            cx.addElement(cx.elt('Frontmatter', 0, end));
            return true;
          }
        }
        // Unterminated: everything was front matter.
        cx.addElement(cx.elt('Frontmatter', 0, cx.prevLineEnd()));
        return true;
      },
    },
  ],
};
