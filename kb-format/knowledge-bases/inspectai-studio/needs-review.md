# Needs review

Known gaps in the manual. Edit this file as gaps are resolved; the agent reads it before
asserting values. Never overwritten by the build.

- **all modules**: the export has no page numbers (`start_page` is null for every section), so
  citations name the module and section instead of a page. Re-export with page numbers to fix.
- **all modules**: the export has no screenshots (`has_images: false`); `images/` and `pages/` are empty.
- **text-reading-ocr/** : the manual has no Deployment section for Text Reading (chapters 2 and 3
  end with Deployment, chapter 4 ends with Validation). Do not assume the export format.
- **settings-reference/** : batch size and image size are not documented anywhere; only
  epochs, learning rate (default 0.001) and GPU selection are.
- **anomaly-detection/** and **text-reading-ocr/** : the input image size is documented only
  for Image Classification (224 x 224, *2.6 Deployment*).
- **image-classification/05-training.md**: the backbone ResNet-34 is the default; the manual does
  not list the other backbones.
