import { createAssetImageField } from './asset-images';

/** `[[file:../assets/NAME]]` or `[[file:assets/NAME]]`, with optional description. */
const ORG_ASSET_IMAGE_RE = /\[\[file:(?:\.\.\/)*assets\/([^\]/]+)\](?:\[([^\]]+)\])?\]/g;

/** StateField previewing `[[file:…/assets/NAME]]` images. */
export const orgImageField = createAssetImageField({
  re: ORG_ASSET_IMAGE_RE,
  filename: (m) => m[1],
  alt: (m) => m[2],
});
