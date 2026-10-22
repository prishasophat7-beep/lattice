# InspectAI Studio 4 - User Guide

InspectAI Studio builds visual inspection models for production lines: it classifies parts, finds anomalies and reads printed text.

## 1 Getting Started

### 1.1 System Requirements

A workstation with Windows 11, 32 GB of memory and an NVIDIA GPU with at least 8 GB of video memory. Training on the CPU is possible but about twenty times slower.

### 1.2 Installation

Run InspectAI_Studio_4_Setup.exe as an administrator and accept the licence. The installer adds the CUDA runtime it needs; no separate driver package is required.

### 1.3 Activating the Licence

Open Help > Licence, paste the 25-character licence key and click Activate. An offline workstation shows a request code instead; send it to support and paste the reply file.

## 2 Image Classification

Image Classification sorts each image of a part into one of the classes you define, for example OK, Scratch and Dent.

### 2.1 Project Setup

Click New Project, choose Image Classification, name the project and pick the folder where it is stored. Add the class names in the Classes panel; at least two classes are needed.

### 2.2 Importing Images

Images can come from a folder on disk or straight from a line camera.

#### 2.2.1 Option A: From a folder

Click Import > Folder and select a parent folder with one subfolder per class; each subfolder name must match a class name. Images in other subfolders are skipped.

#### 2.2.2 Option B: From a camera

Click Import > Camera, select the GigE camera in the list and press Capture for each part. Assign the class with the number keys 1 to 9 while capturing.

### 2.3 Labelling

Open the Gallery, select the images of one class and press L to label them. Unlabelled images are shown with a grey border and are left out of training.

### 2.4 Training

Click Train, keep the default backbone ResNet-34 and set Epochs to 40. Training stops early when the validation accuracy has not improved for 8 epochs.

### 2.5 Validation

Open the Validation tab after training: the confusion matrix shows which classes are mixed up. Aim for at least 98 percent accuracy on the hold-out images before deployment.

### 2.6 Deployment

Click Export and choose ONNX for the line PC or TensorRT for the edge box. The exported model keeps the class names and the input size of 224 x 224 pixels.

## 3 Anomaly Detection

Anomaly Detection learns from good parts only and marks anything that differs from them, without classes.

### 3.1 Project Setup

Click New Project and choose Anomaly Detection. Only good parts are needed for training; keep a few defective parts aside for validation.

### 3.2 Importing Images

Good-part images can come from a folder or from the line camera.

#### 3.2.1 Option A: From a folder

Click Import > Folder and select a folder with good parts only. At least 200 images are recommended.

#### 3.2.2 Option B: From a camera

Click Import > Camera and capture good parts during a normal production run; the capture pauses automatically when the line stops.

### 3.3 Training

Click Train and set the Patch Size to 32. Anomaly training takes about ten minutes for 500 images on an 8 GB GPU.

### 3.4 Validation

Load the defective parts kept aside and adjust the Threshold slider until every defective part is flagged. A threshold between 0.4 and 0.6 suits most parts.

### 3.5 Deployment

Click Export and choose ONNX. The threshold set during validation is stored in the exported model.

## 4 Text Reading (OCR)

Text Reading reads printed or engraved characters such as batch codes and serial numbers.

### 4.1 Project Setup

Click New Project, choose Text Reading and enter the character set, for example A-Z and 0-9.

### 4.2 Annotating Text

Characters can be annotated by hand or proposed by the built-in reader and then corrected.

#### 4.2.1 Method 1: Manual annotation

Draw a box around each line of text and type its characters. This suits fonts the built-in reader does not know.

#### 4.2.2 Method 2: Assisted annotation

Click Propose: the built-in reader fills in each line and you correct the mistakes. This is about three times faster for standard fonts.

### 4.3 Training

Click Train and set Epochs to 60. A text model needs about 300 annotated lines to read reliably.

### 4.4 Validation

The Validation tab shows the character error rate; aim for less than 1 percent before deployment.

## 5 Settings Reference

### 5.1 Epochs

The number of passes over the training images. More epochs can help small data sets but take longer.

### 5.2 Learning Rate

How large each training step is. The default of 0.001 suits almost every project.

### 5.3 GPU Selection

Choose the GPU in Settings > Hardware when the workstation has more than one.
