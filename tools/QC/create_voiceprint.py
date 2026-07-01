import numpy as np
import librosa
import onnxruntime as ort
import sys

# 1. Chargement (pointe sur ton fichier téléchargé)
session = ort.InferenceSession("voxceleb.onnx", providers=['CPUExecutionProvider'])

# AUTO-DÉTECTION du nom de l'entrée attendue par ce modèle précis
input_name = session.get_inputs()[0].name 

def get_embedding(wav_path):
    # Charge et formatte l'audio
    audio, _ = librosa.load(wav_path, sr=16000)
    audio = audio.astype(np.float32).reshape(1, -1)
    
    # 2. Inférence avec le nom d'entrée exact détecté
    outputs = session.run(None, {input_name: audio})
    return outputs[0]

# 3. Exécution et sauvegarde
if len(sys.argv) == 3:
    wav_file = sys.argv[1]
    output_npy = sys.argv[2]
    embedding = get_embedding(wav_file)
    np.save(output_npy, embedding)
    print(f"[OK] Empreinte de {wav_file} sauvée dans {output_npy}")