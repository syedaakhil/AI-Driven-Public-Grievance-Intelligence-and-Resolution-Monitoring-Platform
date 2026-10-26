"""
Baseline TensorFlow text classifier: complaint text → label (the service category for "complaints").

The model is TF-IDF features (a TextVectorization layer, so the saved model takes raw text) →
Dense(64, relu) → Dropout → softmax. It runs only when called (train() or "manage.py train_model"),
never from "Collect data". Each run saves into <ML_ARTIFACTS_DIR>/<dataset>/<time>-<run id>/:
  model.keras    the trained Keras model (input: raw text strings)
  labels.json    output position → label
  metrics.json   accuracy, loss, majority-class baseline, per-label precision/recall
and <ML_ARTIFACTS_DIR>/<dataset>/latest.json names the newest folder.
"""
import json
import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from mlops.models import Dataset, TrainingRun

from . import data
from .data import TrainingError

logger = logging.getLogger(__name__)

MAX_TOKENS = 20000
HIDDEN_UNITS = 64
DROPOUT = 0.3
BATCH_SIZE = 32


def _tensorflow():
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise TrainingError('TensorFlow is not installed. Run: '
                            '.venv\\Scripts\\python.exe -m pip install -r requirements-ml.txt') from exc
    return tf


def train(slug='complaints', epochs=None, artifacts_dir=None, seed=42):
    """Train on the collected dataset `slug`; returns the TrainingRun (succeeded or failed)."""
    dataset = Dataset.objects.filter(slug=slug).first()
    if dataset is None:
        raise TrainingError(f'Dataset "{slug}" has not been collected yet. Press Collect data on the Agent Work page.')
    run = TrainingRun.objects.create(dataset=dataset)
    try:
        _train(run, epochs or settings.ML_TRAIN_EPOCHS, Path(artifacts_dir or settings.ML_ARTIFACTS_DIR), seed)
    except TrainingError as exc:
        _finish(run, TrainingRun.Status.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001  (record the failure, then let the caller see it)
        logger.exception('Training run %s failed', run.pk)
        _finish(run, TrainingRun.Status.FAILED, error=f'Unexpected error ({type(exc).__name__}): {exc}'[:1000])
    return run


def _finish(run, status, **fields):
    run.status = status
    run.finished_at = timezone.now()
    for name, value in fields.items():
        setattr(run, name, value)
    run.save()


def _train(run, epochs, artifacts_dir, seed):
    texts, labels = data.load_examples(run.dataset)
    run.record_count = len(texts)
    data.check_trainable(labels, settings.ML_MIN_TRAIN_RECORDS)
    tf = _tensorflow()
    import numpy as np

    tf.keras.utils.set_random_seed(seed)
    label_names = sorted(set(labels))
    index = {label: position for position, label in enumerate(label_names)}
    train_texts, train_labels, val_texts, val_labels = data.split(texts, labels, seed=seed)

    vectorizer = tf.keras.layers.TextVectorization(max_tokens=MAX_TOKENS, output_mode='tf_idf', name='vectorizer')
    vectorizer.adapt(tf.constant(train_texts))
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(1,), dtype='string', name='text'),
        vectorizer,
        tf.keras.layers.Dense(HIDDEN_UNITS, activation='relu'),
        tf.keras.layers.Dropout(DROPOUT),
        tf.keras.layers.Dense(len(label_names), activation='softmax', name='label'),
    ])
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

    x_train = _text_column(tf, train_texts)
    y_train = np.array([index[label] for label in train_labels])
    x_val = _text_column(tf, val_texts)
    y_val = np.array([index[label] for label in val_labels])
    callbacks = [tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)]
    history = model.fit(x_train, y_train, validation_data=(x_val, y_val), epochs=epochs,
                        batch_size=BATCH_SIZE, callbacks=callbacks, verbose=0)

    loss, accuracy = model.evaluate(x_val, y_val, verbose=0)
    predicted = np.asarray(model.predict(x_val, verbose=0)).argmax(axis=1)
    metrics = {
        'accuracy': float(accuracy),
        'loss': float(loss),
        'baseline_accuracy': data.majority_accuracy(train_labels, val_labels),
        'training_records': len(train_texts),
        'validation_records': len(val_texts),
        'labels': label_names,
        'epochs_run': len(history.history['loss']),
        'per_label': _per_label(label_names, y_val, predicted),
    }

    folder = artifacts_dir / run.dataset.slug / f'{timezone.now():%Y%m%d-%H%M%S}-{run.pk}'
    folder.mkdir(parents=True, exist_ok=True)
    model.save(folder / 'model.keras')
    (folder / 'labels.json').write_text(json.dumps(label_names, indent=2), encoding='utf-8')
    (folder / 'metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    (folder.parent / 'latest.json').write_text(json.dumps({'run': run.pk, 'path': folder.name}), encoding='utf-8')
    _finish(run, TrainingRun.Status.SUCCEEDED, metrics=metrics, artifact_path=str(folder),
            tensorflow_version=tf.__version__)
    logger.info('Training run %s: accuracy %.3f (baseline %.3f), saved in %s',
                run.pk, metrics['accuracy'], metrics['baseline_accuracy'], folder)


def _text_column(tf, texts):
    """Strings shaped (n, 1), the model's input. (Keras rejects NumPy object arrays.)"""
    return tf.reshape(tf.constant(texts, dtype=tf.string), (-1, 1))


def _per_label(label_names, actual, predicted):
    result = {}
    for position, label in enumerate(label_names):
        true_positive = int(((predicted == position) & (actual == position)).sum())
        predicted_count = int((predicted == position).sum())
        actual_count = int((actual == position).sum())
        result[label] = {
            'support': actual_count,
            'precision': true_positive / predicted_count if predicted_count else 0.0,
            'recall': true_positive / actual_count if actual_count else 0.0,
        }
    return result


def latest_model_dir(slug='complaints', artifacts_dir=None):
    base = Path(artifacts_dir or settings.ML_ARTIFACTS_DIR) / slug
    pointer = base / 'latest.json'
    if not pointer.is_file():
        raise TrainingError(f'No trained model for "{slug}" yet. Run: manage.py train_model {slug}')
    return base / json.loads(pointer.read_text(encoding='utf-8'))['path']


def predict(texts, slug='complaints', artifacts_dir=None):
    """[(label, confidence)] for each text, from the newest trained model."""
    tf = _tensorflow()
    import numpy as np

    folder = latest_model_dir(slug, artifacts_dir)
    model = tf.keras.models.load_model(folder / 'model.keras')
    label_names = json.loads((folder / 'labels.json').read_text(encoding='utf-8'))
    scores = np.asarray(model.predict(_text_column(tf, [data.preprocess(text) for text in texts]), verbose=0))
    return [(label_names[int(row.argmax())], float(row.max())) for row in scores]
