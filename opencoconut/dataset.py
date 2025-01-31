import torch
import random
from datasets import load_dataset
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizer

class CoTDataset(Dataset):
    def __init__(
        self,
        dataset_name: str,
        tokenizer: PreTrainedTokenizer,
        coconut_config,
        current_stage=0,
        max_length=512,
        include_reasoning_steps=True,
        prompt_format="{question}{bot}{eot}\n{cot_steps}\n{answer}",
    ):
        self.dataset = load_dataset(dataset_name, split="train")
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.prompt_format = prompt_format
        self.coconut_config = coconut_config
        self.current_stage = current_stage
        self.include_reasoning_steps = include_reasoning_steps

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, idx):
        item = self.dataset[idx]

        # Decide whether to use the current stage or a different stage
        if random.random() < self.coconut_config.mix_prob and self.current_stage != 0:
            # Choose a random stage (excluding the current stage, which is self.k)
            self.current_stage = random.choice([i for i in range(1, self.coconut_config.stages, 1) if i != self.current_stage])
        else:
            self.current_stage = self.current_stage  # Use the current stage

        prompt_formatted = self.prompt_format.format(
            question=item["question"],
            bot=self.coconut_config.bot,
            eot=self.coconut_config.eot,
            cot_steps=(
                "\n".join(item["cot"][self.current_stage:])
                if self.include_reasoning_steps
                else ""
            ),
            answer=item["answer"],
        )

        # Tokenize prompts without padding
        tokenized = self.tokenizer(
            prompt_formatted,
            max_length=self.max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        input_ids = tokenized["input_ids"].squeeze(0)
        attention_mask = tokenized["attention_mask"].squeeze(0)
        labels = input_ids.clone()

        # Mask question and thought tokens
        eot_positions = (input_ids == self.coconut_config.eot_id).nonzero(as_tuple=True)[0]
        if len(eot_positions) > 0:
            eot_pos = eot_positions[0]  # Position of the first <eot> token
            labels[: eot_pos + 1] = -100  # Mask up to and including <eot>

        # Mask padding tokens using attention_mask
        labels[attention_mask == 0] = -100

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }


def split_sequences(
    input_ids: torch.Tensor,
    attention_mask: torch.Tensor,
    labels: torch.Tensor,
    coconut_config,
):
    """Split input_ids, attention_mask, and labels based on <bot> and <eot> token positions.

    Args:
        input_ids: Input IDs tensor.
        attention_mask: Attention mask tensor.
        labels: Labels tensor.
        tokenizer: Tokenizer for padding.
        bot_id: Token ID for <bot>.
        eot_id: Token ID for <eot>.
    """
    batch_size = len(input_ids)
    latent_ids = []
    language_ids = []
    latent_mask = []
    language_mask = []
    latent_labels = []
    language_labels = []

    for i in range(batch_size):
        # Find positions of delimiter tokens in input_ids
        bot_positions = (input_ids[i] == coconut_config.bot_id).nonzero(as_tuple=True)[0]
        eot_positions = (input_ids[i] == coconut_config.eot_id).nonzero(as_tuple=True)[0]

        if len(bot_positions) > 0 and len(eot_positions) > 0:
            # Take first occurrence of each token
            bot_pos = bot_positions[0]
            eot_pos = eot_positions[0]

            # Split input_ids
            latent_ids.append(input_ids[i][: bot_pos + 1])
            language_ids.append(input_ids[i][eot_pos:])

            # Split attention_mask
            latent_mask.append(attention_mask[i][: bot_pos + 1])
            language_mask.append(attention_mask[i][eot_pos:])

            # Split labels
            latent_labels.append(labels[i][: bot_pos + 1])
            language_labels.append(labels[i][eot_pos:])

    # Pad sequences
    latent_ids = torch.nn.utils.rnn.pad_sequence(
        latent_ids, batch_first=True, padding_value=coconut_config.pad_token_id
    )
    language_ids = torch.nn.utils.rnn.pad_sequence(
        language_ids, batch_first=True, padding_value=coconut_config.pad_token_id
    )
    latent_mask = torch.nn.utils.rnn.pad_sequence(
        latent_mask, batch_first=True, padding_value=0
    )
    language_mask = torch.nn.utils.rnn.pad_sequence(
        language_mask, batch_first=True, padding_value=0
    )
    latent_labels = torch.nn.utils.rnn.pad_sequence(
        latent_labels, batch_first=True, padding_value=-100
    )
    language_labels = torch.nn.utils.rnn.pad_sequence(
        language_labels, batch_first=True, padding_value=-100
    )

    return (
        latent_ids,
        language_ids,
        latent_mask,
        language_mask,
        latent_labels,
        language_labels,
    )
