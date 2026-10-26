"""Loading and preparing a collected dataset for training. Plain Python: no TensorFlow needed."""
import random
from collections import Counter, defaultdict

from mlops.models import DatasetRecord


class TrainingError(Exception):
    """A reason training can't run, written for staff."""


def load_examples(dataset):
    """(texts, labels) of the dataset's labelled records, in a stable order."""
    rows = DatasetRecord.objects.filter(dataset=dataset).exclude(label='').order_by('source_id')
    texts, labels = [], []
    for text, label in rows.values_list('text', 'label'):
        text = preprocess(text)
        if text:
            texts.append(text)
            labels.append(label)
    return texts, labels


def preprocess(text):
    """Lower-case with single spaces. (Collection already hid emails and phone numbers.)"""
    return ' '.join(str(text).lower().split())


def check_trainable(labels, min_records):
    if len(labels) < min_records:
        raise TrainingError(
            f'Only {len(labels)} labelled records; training needs at least {min_records} '
            f'(ML_MIN_TRAIN_RECORDS). Collect again when more arrive.')
    if len(set(labels)) < 2:
        raise TrainingError('All labelled records have the same label; training needs at least 2 labels.')


def split(texts, labels, validation_fraction=0.2, seed=42):
    """
    Stratified split: each label keeps about the same share in both parts. A label with a single
    record goes to training only. Returns (train_texts, train_labels, val_texts, val_labels).
    """
    by_label = defaultdict(list)
    for index, label in enumerate(labels):
        by_label[label].append(index)
    rng = random.Random(seed)
    train_idx, val_idx = [], []
    for label in sorted(by_label):
        indices = by_label[label][:]
        rng.shuffle(indices)
        take = round(len(indices) * validation_fraction) if len(indices) > 1 else 0
        take = min(max(take, 1 if len(indices) > 1 else 0), len(indices) - 1)
        val_idx += indices[:take]
        train_idx += indices[take:]
    train_idx.sort()
    val_idx.sort()
    return ([texts[i] for i in train_idx], [labels[i] for i in train_idx],
            [texts[i] for i in val_idx], [labels[i] for i in val_idx])


def majority_accuracy(train_labels, val_labels):
    """Accuracy of always guessing the most common training label: the number a model must beat."""
    if not val_labels:
        return 0.0
    most_common = Counter(train_labels).most_common(1)[0][0]
    return sum(1 for label in val_labels if label == most_common) / len(val_labels)
