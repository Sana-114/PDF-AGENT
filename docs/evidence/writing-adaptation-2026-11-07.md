# Outline a proposed study of memory-efficient language-model adaptation, using LoRA and QLoRA as related work. Describe t

## Abstract

This proposed study investigates: Outline a proposed study of memory-efficient language-model adaptation, using LoRA and QLoRA as related work. Describe the frozen weights, trainable adapters and the specific hardware and evaluation conditions only when supported. No new experiments have been conducted.

Evidence basis: LoRA freezes the pretrained model weights and injects trainable rank-decomposition matrices into Transformer layers, reducing trainable parameters for downstream tasks; QLoRA instead backpropagates gradients through a frozen 4-bit quantized pretrained model into Low Rank Adapters (LoRA). [E5 E8]

Evidence basis: LoRA can reduce the number of trainable parameters by 10,000 times and the GPU memory requirement by 3 times compared with GPT-3 175B fine-tuned with Adam. [E5]

## Introduction

- Define the research problem and scope around **Outline a proposed study of memory-efficient language-model adaptation, using LoRA and QLoRA as related work. Describe the frozen weights, trainable adapters and the specific hardware and evaluation conditions only when supported. No new experiments have been conducted.**.
- Identify the evidence-supported gap and formulate testable research questions.
- LoRA freezes the pretrained model weights and injects trainable rank-decomposition matrices into Transformer layers, reducing trainable parameters for downstream tasks; QLoRA instead backpropagates gradients through a frozen 4-bit quantized pretrained model into Low Rank Adapters (LoRA). [E5 E8]
- LoRA can reduce the number of trainable parameters by 10,000 times and the GPU memory requirement by 3 times compared with GPT-3 175B fine-tuned with Adam. [E5]
- QLORA reduces memory use enough to finetune a 65B parameter model on a single 48GB GPU while preserving full 16-bit finetuning task performance; QLORA's reported innovations to save memory include 4-bit NormalFloat (NF4), Double Quantization and Paged Optimizers. [E8]

## Related Work

- **[P1] LORA: LOW-RANK ADAPTATION OF LARGE LANGUAGE MODELS / [P2] QLORA: Efficient Finetuning of Quantized LLMs**: LoRA freezes the pretrained model weights and injects trainable rank-decomposition matrices into Transformer layers, reducing trainable parameters for downstream tasks; QLoRA instead backpropagates gradients through a frozen 4-bit quantized pretrained model into Low Rank Adapters (LoRA). [E5 E8]
- **[P1] LORA: LOW-RANK ADAPTATION OF LARGE LANGUAGE MODELS**: LoRA can reduce the number of trainable parameters by 10,000 times and the GPU memory requirement by 3 times compared with GPT-3 175B fine-tuned with Adam. [E5]
- **[P2] QLORA: Efficient Finetuning of Quantized LLMs**: QLORA reduces memory use enough to finetune a 65B parameter model on a single 48GB GPU while preserving full 16-bit finetuning task performance; QLORA's reported innovations to save memory include 4-bit NormalFloat (NF4), Double Quantization and Paged Optimizers. [E8]

## References

[P1] Edward Hu, Yelong Shen, Phillip Wallis, Zeyuan Allen-Zhu, Yuanzhi Li, Shean Wang, Lu Wang, Weizhu Chen. (2021). LORA: LOW-RANK ADAPTATION OF LARGE LANGUAGE MODELS. arXiv:2106.09685.

[P2] Tim Dettmers, Artidoro Pagnoni, Ari Holtzman, Luke Zettlemoyer. (2023). QLORA: Efficient Finetuning of Quantized LLMs. arXiv:2305.14314.