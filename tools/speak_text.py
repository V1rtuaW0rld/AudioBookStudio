import argparse
import json
import urllib.request
import urllib.error
import sys
import os

def main():
    parser = argparse.ArgumentParser(description="Générer de l'audio via Voicebox API à partir d'un fichier texte et d'une config JSON.")
    parser.add_argument("--config", required=True, help="Chemin du fichier JSON de configuration (ex: config.json)")
    parser.add_argument("--text", required=True, help="Chemin du fichier texte (ex: texte.txt)")
    parser.add_argument("--output", default="output.wav", help="Chemin du fichier audio de sortie (par défaut: output.wav)")
    parser.add_argument("--profile_id", required=False, default=None)

    args = parser.parse_args()
    
    # 1. Lire la config JSON
    if not os.path.exists(args.config):
        print(f"Erreur : Le fichier de configuration '{args.config}' n'existe pas.")
        sys.exit(1)
        
    try:
        with open(args.config, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f"Erreur lors de la lecture ou du parsing du JSON de config : {e}")
        sys.exit(1)

    # 1b. Surcharge dynamique du profile_id
    if args.profile_id:
        print(f"[INFO] Surcharge du profil ID : Utilisation de '{args.profile_id}' au lieu de '{payload.get('profile_id')}'.")
        payload["profile_id"] = args.profile_id
        
    # 2. Lire le fichier texte
    if not os.path.exists(args.text):
        print(f"Erreur : Le fichier texte '{args.text}' n'existe pas.")
        sys.exit(1)
        
    try:
        with open(args.text, 'r', encoding='utf-8') as f:
            text_content = f.read().strip()
    except Exception as e:
        print(f"Erreur lors de la lecture du fichier texte : {e}")
        sys.exit(1)
        
    if not text_content:
        print("Erreur : Le fichier texte est vide.")
        sys.exit(1)
        
    # Injecter le texte dans la requête API
    payload["text"] = text_content

    # URL configurable via VOICEBOX_URL (Docker) ; fallback local par défaut.
    base_url = os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493").rstrip("/")
    url = f"{base_url}/generate/stream"
    headers = {"Content-Type": "application/json"}
    
    print(f"Envoi de la requête à {url} (Voix : {payload.get('profile_id', 'Inconnue')})...")
    
    # Envoi de la requête HTTP POST
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                with open(args.output, 'wb') as out_f:
                    # Stream par morceaux de 64KB
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        out_f.write(chunk)
                print(f"Succès ! Fichier généré : {args.output}")
            else:
                print(f"Erreur HTTP inattendue : {response.status} {response.reason}")
                sys.exit(1)
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8', errors='ignore')
        print(f"Erreur de génération API (Code {e.code}) : {e.reason}")
        if error_msg:
            print(f"Détails de l'erreur : {error_msg}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Erreur de connexion à l'API : {e.reason}")
        print("Assurez-vous que le serveur Voicebox tourne sur le port 17493 (just dev-backend).")
        sys.exit(1)
    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
