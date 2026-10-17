# audio_signaling

AgriDrone field-survey repo. `send.py` / `recv.py` are the ggwave audio link. Wheat-head detection is under `wheat/`.

## Wheat head detector (YOLOv8)

Fine-tune of YOLOv8s on the [Global Wheat Head Dataset 2021](https://www.kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (single class: `wheat_head`).

Kaggle's download API needs a token in `~/.kaggle/kaggle.json`. This checkout pulls the same 2021 competition release, already converted to YOLO labels, from [dronefreak/GWHD](https://huggingface.co/datasets/dronefreak/GWHD).

```bash
pip install -r wheat/requirements.txt
python wheat/download_dataset.py
python wheat/prepare_labels.py
python wheat/train.py
python wheat/predict.py path/to/field.png
python wheat/detect.py path/to/field.png --save
```

Images stay in `data/` (gitignored, about 10 GB). The finetuned checkpoint is `wheat/weights/yolov8s-wheat.pt`. The portable model is `wheat/weights/yolov8s-wheat.onnx` (1024 px input). `wheat/detect.py` loads that file and prints every wheat head as `x1 y1 x2 y2 confidence`. `--save` writes an annotated copy under `runs/wheat/onnx/`. Regenerate the ONNX file with `python wheat/export_onnx.py`.

Training defaults: YOLOv8s, 1024 px, batch 8, 50 epochs, COCO pretrained initialization.
