import numpy as np
import librosa
import onnxruntime as ort
import sys
import os
from sklearn.metrics.pairwise import cosine_similarity

# Verification des arguments de la ligne de commande
if len(sys.argv) < 3:
    print("[ERREUR] Parametres manquants.")
    print("[INFO] Usage: python verify_voice.py <chemin_reference.npy> <chemin_test.wav>")
    sys.exit(1)

ref_npy_path = sys.argv[1]
test_wav_path = sys.argv[2]

# Securite : Verification de l'existence des fichiers avant de lancer ONNX
if not os.path.exists(ref_npy_path):
    print(f"[ERREUR] Le fichier de reference '{ref_npy_path}' n'existe pas.")
    sys.exit(1)

if not os.path.exists(test_wav_path):
    print(f"[ERREUR] Le fichier audio de test '{test_wav_path}' n'existe pas.")
    sys.exit(1)

# 1. Chargement du modele et de la reference dynamique
session = ort.InferenceSession("voxceleb.onnx", providers=['CPUExecutionProvider'])
input_name = session.get_inputs()[0].name 
ref_embedding = np.load(ref_npy_path)

def get_embedding(wav_path):
    audio, _ = librosa.load(wav_path, sr=16000)
    audio = audio.astype(np.float32).reshape(1, -1)
    outputs = session.run(None, {input_name: audio})
    return outputs[0]

try:
    # 2. Extraction de l'empreinte du fichier a tester
    test_embedding = get_embedding(test_wav_path)
    
    # 3. Calcul de la similarite (Score entre -1 et 1)
    score = cosine_similarity(ref_embedding.reshape(1, -1), test_embedding.reshape(1, -1))[0][0]
    
    # 4. Seuil de decision
    SEUIL = 0.70 
    
    print("-" * 60)
    print(f"Reference : {os.path.basename(ref_npy_path)}")
    print(f"Teste avec : {os.path.basename(test_wav_path)}")
    print(f"Score de similarite : {score:.4f}")
    
    if score >= SEUIL:
        print(f"[OK] ACCES AUTORISE (Score {score:.4f} >= {SEUIL})")
    else:
        print(f"[INFO] ACCES REFUSE (Score {score:.4f} < {SEUIL})")
    print("-" * 60)

except Exception as e:
    print(f"[ERREUR] Erreur lors du traitement : {e}")