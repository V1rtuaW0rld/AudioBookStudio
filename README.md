# 🎙️ AudioBookStudio

<div align="center">
  <p>
    <a href="#-audiobookstudio-français"><b>🇫🇷 Français</b></a> · 
    <a href="#-audiobookstudio-english"><b>🇬🇧 English</b></a>
  </p>
</div>

---

# 🇫🇷 AudioBookStudio (Français)

**AudioBookStudio** est une suite de production complète et 100% locale pour concevoir des **livres audio immersifs et polyphoniques (multi-voix)** à partir de romans et de pièces de théâtre.

Contrairement aux outils de doublage rapide ou de synthèse vocale généraliste, AudioBookStudio a été pensé comme une véritable **chaîne industrielle de fabrication de livres audio**, spécialement optimisée pour la typographie et la littérature française.

---

## 📸 Aperçu de l'interface

### 1. Tableau de bord des projets
Gérez l'ensemble de vos romans et pièces de théâtre avec suivi précis de la progression de génération et export audio final.
![Dashboard AudioBookStudio](docs/images/dashboard.png)

### 2. Studio de Production & Assignation des Rôles
Un tableau de bord par projet permettant d'associer chaque personnage à une voix dédiée, d'écouter les répliques segment par segment, d'ajuster le tempo et de régénérer une prise isolée.
![Studio AudioBookStudio](docs/images/project_studio.png)

### 3. Atelier Vocal VoiceBox (http://localhost:17493)
La création, le clonage zéro-shot et la gestion des profils vocaux sont propulsés par **VoiceBox**.
![Profils vocaux dans VoiceBox](docs/images/voicebox_profiles.png)

### 4. Quality Center (Contrôle Qualité Acoustique)
Détection automatique des dérives de voix et des hallucinations grâce au modèle d'empreinte vocale **VoxCeleb (ONNX)**. Isolez et corrigez en un clic les segments hors-seuil.
![Quality Center](docs/images/quality_center.png)

---

## 🙏 Remerciements à l'équipe VoiceBox

Un immense merci et un coup de chapeau à l'équipe du projet open-source **[VoiceBox](https://github.com/jamiepine/voicebox)** (créé par Jamie Pine et ses contributeurs). 

**VoiceBox est le moteur TTS central qui fait battre le cœur d'AudioBookStudio.** C'est dans son interface dédiée que vous façonnez et clonez les voix de vos personnages à partir de simples extraits audio :
👉 **Interface VoiceBox :** [http://localhost:17493](http://localhost:17493)

AudioBookStudio vient s'articuler autour de VoiceBox pour apporter tout ce qui manquait pour transformer un texte brut en une œuvre littéraire complète.

---

## ✨ Fonctionnalités clés

1. **Ingestion & Normalisation avancée** :
   - Import direct de fichiers **PDF** ou **EPUB**.
   - Atelier de normalisation pour corriger la typographie française : suppression des en-têtes, gestion des césures de mots, tirets cadratins, espaces insécables et ligatures (`œ`, `æ`).

2. **Découpe chirurgicale des phrases (Sentence Chunking)** :
   - Découpe strictly basée sur les fins de phrases (`.`, `!`, `?`).
   - Ne coupe **jamais** sur une virgule, un point-virgule ou en plein souffle afin de garantir une intonation naturelle au moteur TTS.

3. **Attribution des Rôles par IA locale (`WhoIsSpeaking`)** :
   - Pour les romans polyphoniques et les pièces de théâtre, un LLM local (via LM Studio ou Ollama) analyse le récit, sépare les dialogues de la narration et attribue chaque réplique au bon personnage, avec didascalies.

4. **Contrôle Qualité automatique (QC Center)** :
   - Compare chaque segment audio généré à l'empreinte biométrique de référence de la voix (modèle VoxCeleb ONNX).
   - Détecte instantanément si le modèle a dérivé, bafouillé ou changé de timbre.

5. **Pack de 80+ voix françaises préconfigurées** :
   - Des voix de narrateurs et d'acteurs de premier plan (Pierre Arditi, Jean-Pierre Marielle, Catherine Frot, Gérard Depardieu, les voix de la trilogie marseillaise de Marcel Pagnol, etc.) téléchargeables directement dans les [Releases](https://github.com/V1rtuaW0rld/AudioBookStudio/releases/tag/v1.0.0).

---

## 🚀 Installation & Démarrage

### Prérequis
* **Docker Desktop** installé et fonctionnel.
* Carte graphique NVIDIA recommandée avec **au moins 12 à 16 Go de VRAM** (pour faire tourner confortablement le modèle TTS et le LLM local d'attribution des rôles).

### 1. Cloner le dépôt
```bash
git clone https://github.com/V1rtuaW0rld/AudioBookStudio.git
cd AudioBookStudio
```

### 2. Installer le pack de voix françaises (Recommandé)
Pour disposer immédiatement des 80+ voix françaises dans votre studio sans devoir les cloner une à une :
1. Rendez-vous sur la page des [Releases](https://github.com/V1rtuaW0rld/AudioBookStudio/releases/tag/v1.0.0).
2. Téléchargez le fichier **`AudioBookStudio-French-Voices-v1.0.zip`** (62 Mo) et placez-le à la racine du dossier `AudioBookStudio`.
3. Sous PowerShell, exécutez simplement :
```powershell
.\import-voice-pack.ps1
```
*(Vos profils et échantillons de référence sont automatiquement décompressés et configurés dans `VoiceBox/data`).*

### 3. Lancer l'application
Sous Windows (PowerShell) :
```powershell
.\run-docker.ps1
```
*(Le script détecte automatiquement votre GPU Nvidia et démarre la stack complète sous Docker).*

Sous Linux / Mac :
```bash
docker compose up -d
```

### 4. Accéder aux interfaces
* **Studio AudioBookStudio (Projets, Orchestration & QC) :** [http://localhost:5180](http://localhost:5180)
* **Atelier VoiceBox (Clonage & Gestion des voix) :** [http://localhost:17493](http://localhost:17493)

---

## 📖 Mode d'emploi pas à pas

### Étape 1 : Créer ou vérifier les voix dans VoiceBox
Rendez-vous sur [http://localhost:17493](http://localhost:17493). Si vous avez exécuté `import-voice-pack.ps1`, vous verrez immédiatement vos profils d'acteurs. Vous pouvez également cliquer sur **"Create Voice"** pour créer de nouveaux personnages à partir de clips audio propres de 10 à 30 secondes.

### Étape 2 : Créer un projet de livre
Sur [http://localhost:5180](http://localhost:5180), cliquez sur **"Nouveau Projet"** :
<img width="519" height="425" alt="image" src="https://github.com/user-attachments/assets/8c808331-eee1-4e8c-81fa-899a7bff52d6" />

* Choisissez le type : **Roman (mono-narrateur)**, **Roman (avec personnages)** ou **Théâtre**.
* Chargez votre fichier source (PDF ou EPUB).

### Étape 3 : Normaliser le texte
Ouvrez l'**Atelier de Normalisation**. Utilisez les règles typographiques intégrées pour supprimer les bruits de numérisation, les numéros de page et harmoniser les dialogues. Validez pour enregistrer.
<img width="1904" height="1056" alt="image" src="https://github.com/user-attachments/assets/24e99f1a-aa4e-451d-8b33-d33c384a93c3" />

### Étape 4 : Découper ou attribuer les rôles par IA
* **Pour un roman classique** : Cliquez sur *Créer les segments* pour lancer le découpeur strict.
* **Pour un roman polyphonique** : Lancez le module **🧠 IA & Rôles**. Le modèle local détecte qui parle et sépare la narration des répliques. Cliquez ensuite sur *Assembler le livre*.

### Étape 5 : Distribution et Génération
Dans le panneau de gauche, associez chaque rôle détecté à une voix de VoiceBox (ex : Narrateur ➔ Pierre Arditi, César ➔ Jean Carmet). Cliquez sur **Générer tout l'Audio** ou générez les plages de votre choix.

### Étape 6 : Contrôle Qualité et Export
Ouvrez le **Quality Center**. Tous les segments présentant une anomalie de timbre ou de durée sont surlignés. Écoutez-les individuellement et cliquez sur le bouton de régénération si nécessaire. Une fois satisfait, exportez le fichier audio complet assemblé !

---
---

# 🇬🇧 AudioBookStudio (English)

**AudioBookStudio** is a full-featured, 100% local production suite designed to create **immersive, multi-voice, dramatized audiobooks** from novels and theater plays.

Unlike general-purpose speech hubs or quick video-dubbing utilities, AudioBookStudio was engineered as an **industrial audiobook factory**, fine-tuned for high-standard typography, literary dialogues, and character performance.

---

## 📸 Interface Overview

### 1. Multi-Book Project Dashboard
Manage your entire collection of novels and plays with real-time generation metrics and complete audiobook exports.
![AudioBookStudio Dashboard](docs/images/dashboard.png)

### 2. Production Studio & Character Casting
Per-project workspace to bind every book character to a cloned voice, preview audio segment by segment, adjust reading speed, and re-record individual lines.
![AudioBookStudio Studio](docs/images/project_studio.png)

### 3. VoiceBox Studio (http://localhost:17493)
Voice creation, zero-shot cloning, and voice profile management are powered by **VoiceBox**.
![VoiceBox Profiles](docs/images/voicebox_profiles.png)

### 4. Quality Center (Acoustic Verification)
Automated detection of voice drift and hallucinations using the **VoxCeleb (ONNX)** speaker verification model. Isolate and fix defective segments in one click.
![Quality Center](docs/images/quality_center.png)

---

## 🙏 Credits & Special Thanks to VoiceBox

A huge shout-out and heartfelt gratitude to the creators and contributors of **[VoiceBox](https://github.com/jamiepine/voicebox)** (created by Jamie Pine and team).

**VoiceBox is the core TTS engine that powers AudioBookStudio.** It provides the dedicated studio interface where voice profiles and character clones are forged from simple audio references:
👉 **VoiceBox Interface:** [http://localhost:17493](http://localhost:17493)

AudioBookStudio wraps around VoiceBox to provide the complete literary pipeline: text normalization, theatrical dialogue splitting, role assignment, and quality control.

---

## ✨ Key Features

1. **Ingestion & Advanced Normalization**:
   - Direct import of **PDF** and **EPUB** books.
   - Dedicated normalization workshop to clean optical scan artifacts, page numbers, hyphens, non-breaking spaces, and typographical ligatures.

2. **Strict Sentence-Boundary Chunking**:
   - Splits text strictly on sentence endings (`.`, `!`, `?`).
   - **Never cuts on commas, semicolons, or mid-breath pauses**, ensuring natural human prosody and complete sentences for the TTS model.

3. **Local LLM Role Attribution (`WhoIsSpeaking`)**:
   - For multi-character novels and plays, a local LLM (via LM Studio or Ollama) scans the narrative, isolates character dialogues from descriptive narration, and tags lines with actor names and stage directions.

4. **Automated Acoustic QC (Quality Center)**:
   - Computes speaker embeddings for every rendered segment against the original reference voiceprint (VoxCeleb ONNX).
   - Instantly flags segments where the model hallucinated, drifted, or changed pitch.

5. **Pack of 80+ Pre-Configured French Voices**:
   - Iconic narrators, actors, and character voices (Pierre Arditi, Jean-Pierre Marielle, Catherine Frot, Gérard Depardieu, Marcel Pagnol cast, etc.) downloadable directly from the [Releases](https://github.com/V1rtuaW0rld/AudioBookStudio/releases/tag/v1.0.0).

---

## 🚀 Quick Start & Installation

### Requirements
* **Docker Desktop** installed and running.
* NVIDIA GPU with **12 GB to 16 GB VRAM recommended** (to comfortably run both the TTS synthesis engine and the local LLM role analyzer).

### 1. Clone the repository
```bash
git clone https://github.com/V1rtuaW0rld/AudioBookStudio.git
cd AudioBookStudio
```

### 2. Install the Voice Pack (Recommended)
To immediately get 80+ French voice profiles in your studio without cloning them manually:
1. Head to the [Releases](https://github.com/V1rtuaW0rld/AudioBookStudio/releases/tag/v1.0.0) page.
2. Download **`AudioBookStudio-French-Voices-v1.0.zip`** (62 MB) and put it in the root of the `AudioBookStudio` directory.
3. In PowerShell, simply run:
```powershell
.\import-voice-pack.ps1
```
*(Your profiles and audio reference clips are automatically extracted and wired into `VoiceBox/data`).*

### 3. Start the Application
On Windows (PowerShell):
```powershell
.\run-docker.ps1
```
*(The script automatically detects your NVIDIA GPU and boots the full stack in Docker).*

On Linux / macOS:
```bash
docker compose up -d
```

### 4. Access the Studio Interfaces
* **AudioBookStudio (Projects, Pipeline & QC):** [http://localhost:5180](http://localhost:5180)
* **VoiceBox (Voice Cloning & Profiles):** [http://localhost:17493](http://localhost:17493)

---

## 📖 Step-by-Step Workflow

### Step 1: Manage Voices in VoiceBox
Open [http://localhost:17493](http://localhost:17493). If you ran `import-voice-pack.ps1`, your actor profiles are already there. You can also click **"Create Voice"** to clone new characters from 10 to 30 second clean audio clips.

### Step 2: Create a Book Project
On [http://localhost:5180](http://localhost:5180), click **"Nouveau Projet"** (New Project):
<img width="519" height="425" alt="image" src="https://github.com/user-attachments/assets/6bd7786a-4140-4c28-baef-e2277fc5a8bf" />

* Select the type: **Roman (mono-narrator)**, **Roman (multi-character)**, or **Théâtre** (Play).
* Upload your PDF or EPUB source file.

### Step 3: Text Normalization
Open the **Atelier de Normalisation** (Normalization Workshop). Use the built-in rule builder to strip OCR artifacts and page headers, then save.
<img width="1904" height="1056" alt="image" src="https://github.com/user-attachments/assets/24e99f1a-aa4e-451d-8b33-d33c384a93c3" />
### Step 4: Chunk or Assign Roles with AI
* **Standard single-narrator novel**: Click *Créer les segments* (Create segments) to run the strict sentence chunker.
* **Multi-voice novel**: Open **🧠 IA & Rôles**. The local LLM separates narration from character speech and tags each character. Then click *Assembler le livre* (Assemble book).

### Step 5: Character Casting & Generation
In the left sidebar, cast every detected character to a VoiceBox voice (e.g., Narrator ➔ Pierre Arditi, Caesar ➔ Jean Carmet). Click **Générer tout l'Audio** (Generate all Audio) or synthesize selected batches.

### Step 6: Quality Verification & Final Export
Open the **Quality Center**. All segments flagged for acoustic drift or duration anomalies are highlighted. Listen to them individually and re-generate any flawed segment in one click. Once satisfied, export your assembled master audiobook!

---

## 📜 License & Credits
* **AudioBookStudio**: Developed by **V1rtuaW0rld**.
* **TTS Core**: [VoiceBox](https://github.com/jamiepine/voicebox) by Jamie Pine (MIT License).
* **QC Speaker Embedding**: VoxCeleb ResNet34 ONNX.
