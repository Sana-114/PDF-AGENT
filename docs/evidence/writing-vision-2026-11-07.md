# Outline a proposed study comparing supervised visual representation learning with natural-language supervision. Attribut

## Abstract

This proposed study investigates: Outline a proposed study comparing supervised visual representation learning with natural-language supervision. Attribute training data and evaluation protocols to ViT and CLIP separately. Distinguish zero-shot classification, linear probes and full fine-tuning. No experiments have been performed by the proposed study.

Evidence basis: ViT and CLIP differ in both training data and evaluation emphasis: ViT is pre-trained and then transferred with few-shot or fine-tuning evaluation on benchmarks such as ImageNet, CIFAR-100, and VTAB, whereas CLIP is trained on 400 million internet-collected (image, text) pairs and evaluated extensively through zero-shot transfer, alongside linear-classifier representation evaluation. [E1 E3 E2 E4]

Evidence basis: For CLIP, the study describes fitting a linear classifier on an extracted representation as a common evaluation and end-to-end fine-tuning as an alternative, but states that it opts for linear-classifier-based evaluation because fine-tuning adapts representations per dataset and can compensate for, and potentially mask, failures to learn general and robust representations during pre-training. [E4]

## Introduction

- Define the research problem and scope around **Outline a proposed study comparing supervised visual representation learning with natural-language supervision. Attribute training data and evaluation protocols to ViT and CLIP separately. Distinguish zero-shot classification, linear probes and full fine-tuning. No experiments have been performed by the proposed study.**.
- Identify the evidence-supported gap and formulate testable research questions.
- ViT and CLIP differ in both training data and evaluation emphasis: ViT is pre-trained and then transferred with few-shot or fine-tuning evaluation on benchmarks such as ImageNet, CIFAR-100, and VTAB, whereas CLIP is trained on 400 million internet-collected (image, text) pairs and evaluated extensively through zero-shot transfer, alongside linear-classifier representation evaluation. [E1 E3 E2 E4]
- For CLIP, the study describes fitting a linear classifier on an extracted representation as a common evaluation and end-to-end fine-tuning as an alternative, but states that it opts for linear-classifier-based evaluation because fine-tuning adapts representations per dataset and can compensate for, and potentially mask, failures to learn general and robust representations during pre-training. [E4]
- ViT's pre-training data scale ranges from ImageNet (1k classes, 1.3M images) through ImageNet-21k (21k classes, 14M images) to JFT (18k classes, 303M high-resolution images), with models transferred to ImageNet, CIFAR-10/100, Oxford-IIIT Pets, and Oxford Flowers-102, and ViT-Large models only show their full benefit with JFT-300M pre-training (underperforming ViT-Base on ImageNet pre-training). [E18 E11 E23]

## Related Work

- **[P1] AN IMAGE IS WORTH 16X16 WORDS: TRANSFORMERS FOR IMAGE RECOGNITION AT SCALE / [P2] Learning Transferable Visual Models From Natural Language Supervision**: ViT and CLIP differ in both training data and evaluation emphasis: ViT is pre-trained and then transferred with few-shot or fine-tuning evaluation on benchmarks such as ImageNet, CIFAR-100, and VTAB, whereas CLIP is trained on 400 million internet-collected (image, text) pairs and evaluated extensively through zero-shot transfer, alongside linear-classifier representation evaluation. [E1 E3 E2 E4]
- **[P2] Learning Transferable Visual Models From Natural Language Supervision**: For CLIP, the study describes fitting a linear classifier on an extracted representation as a common evaluation and end-to-end fine-tuning as an alternative, but states that it opts for linear-classifier-based evaluation because fine-tuning adapts representations per dataset and can compensate for, and potentially mask, failures to learn general and robust representations during pre-training. [E4]
- **[P1] AN IMAGE IS WORTH 16X16 WORDS: TRANSFORMERS FOR IMAGE RECOGNITION AT SCALE**: ViT's pre-training data scale ranges from ImageNet (1k classes, 1.3M images) through ImageNet-21k (21k classes, 14M images) to JFT (18k classes, 303M high-resolution images), with models transferred to ImageNet, CIFAR-10/100, Oxford-IIIT Pets, and Oxford Flowers-102, and ViT-Large models only show their full benefit with JFT-300M pre-training (underperforming ViT-Base on ImageNet pre-training). [E18 E11 E23]

## References

[P1] Alexey Dosovitskiy, Lucas Beyer, Alexander Kolesnikov, Dirk Weissenborn, Xiaohua Zhai, Thomas Unterthiner, Mostafa Dehghani, Matthias Minderer, Georg Heigold, Sylvain Gelly, Jakob Uszkoreit, Neil Houlsby. (2020). AN IMAGE IS WORTH 16X16 WORDS: TRANSFORMERS FOR IMAGE RECOGNITION AT SCALE. arXiv:2010.11929.

[P2] Alec Radford, Jong Wook Kim, Chris Hallacy, Aditya Ramesh, Gabriel Goh, Sandhini Agarwal, Girish Sastry, Amanda Askell, Pamela Mishkin, Jack Clark, Gretchen Krueger, Ilya Sutskever. (2021). Learning Transferable Visual Models From Natural Language Supervision. arXiv:2103.00020.