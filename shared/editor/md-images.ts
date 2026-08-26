import { createAssetImageField } from './asset-images';

/** `![alt](../assets/NAME)`, `![alt](/assets/NAME)` or `![alt](assets/NAME)`. */
const MD_ASSET_IMAGE_RE = /!\[([^\]]*)\]\((?:\.\.\/|\/)*assets\/([^)\s]+)\)/g;

/** StateField previewing `![alt](…/assets/NAME)` images. */
export const mdImageField = createAssetImageField({
  re: MD_ASSET_IMAGE_RE,
  filename: (m) => m[2],
  alt: (m) => m[1] || undefined,
});
