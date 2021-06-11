import torch
from transformers import AutoTokenizer
import numpy as np
from datasets import load_metric
from scipy.special import softmax
from torch.nn import CrossEntropyLoss
from transformers import Trainer, TrainingArguments
from torch.utils.data import DataLoader, SequentialSampler

np.random.seed(42)
CLASS_IMBALANCE_WEIGHTS = [11 / (2 * 10), 11 / (2 * 1)]
NUM_LABELS = 2


class Torch_dataset_mono(torch.utils.data.Dataset):
    """Create Torch Dataset for Text Encoder training.

    """
    def __init__(self, data):
        """Initialize Torch Dataset with Tokenizer and data

        Args:
            data: Data to be converted to Torch Dataset
        """
        tokenizer = AutoTokenizer.from_pretrained("xlm-roberta-base")
        sentence_pairs = data.apply(lambda row: [row["text_source"], row["text_target"]], axis=1).tolist()
        self.encodings = tokenizer(sentence_pairs, padding="max_length", truncation="longest_first",
                                   return_tensors="pt")
        self.labels = data["Translation"].tolist()

    def __getitem__(self, idx):
        item = {key: torch.tensor(val[idx]) for key, val in self.encodings.items()}
        item['labels'] = torch.tensor(self.labels[idx])
        return item

    def __len__(self):
        return len(self.labels)


def compute_metrics(eval_pred):
    """Compute F1, Accuracy, Recall, Precision and Log Loss

    Args:
        eval_pred: Prediction of the model

    Returns:
        dict: F1, Accuracy, Recall, Precision and Log Loss

    """
    logits, labels = eval_pred

    metric = load_metric("custom_metric.py")
    pred_prob = softmax(logits, axis=1)
    pred_prob_pos_class = [pred[1] for pred in pred_prob]
    return metric.compute(predictions=pred_prob_pos_class, references=labels)


class WeightedLossTrainer(Trainer):
    """Custom Huggingface Trainer, which calculates weighted Loss and samples such that in each batch, we have the
    correct translation and 10 different negative examples for one source sentence.

    """
    def compute_loss(self, model, inputs, return_outputs=False):
        """Compute the weighted Loss

        Args:
            model: Huggingface Text Encoder
            inputs: Huggingface Input for the model
            return_outputs (boolean): If additionally return output or not

        Returns:
            tuple: Loss and the outputs of the model

        """
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs[0]
        weights = CLASS_IMBALANCE_WEIGHTS
        class_weights = torch.FloatTensor(weights).cuda()
        loss_fct = CrossEntropyLoss(weight=class_weights)
        loss = loss_fct(logits.view(-1, NUM_LABELS), labels.view(-1))

        return (loss, outputs) if return_outputs else loss

    def get_train_dataloader(self) -> DataLoader:
        """Returns the training :class:`~torch.utils.data.DataLoader`. In our case, it does not shuffle, therefore
        if the dataset is ordered, our batch will contain the positive and 10 negative examples for one source sentence
        if the batch is also 11 samples long.

        """
        if self.train_dataset is None:
            raise ValueError("Trainer: training requires a train_dataset.")

        train_dataset = self.train_dataset

        return DataLoader(
            train_dataset,
            batch_size=self.args.train_batch_size,
            collate_fn=self.data_collator,
            num_workers=self.args.dataloader_num_workers,
            shuffle=False
        )