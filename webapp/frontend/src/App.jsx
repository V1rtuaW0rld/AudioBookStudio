import { useState, useEffect, useRef } from 'react';
import { 
  BookOpen, 
  FolderPlus, 
  Play, 
  Pause, 
  Music, 
  RefreshCw, 
  Cpu, 
  ChevronRight, 
  FileText, 
  CheckCircle2, 
  Terminal as TerminalIcon,
  HelpCircle,
  FolderOpen,
  Target,
  SkipBack,
  SkipForward,
  Plus,
  Trash2,
  Sliders,
  ArrowLeft,
  List,
  Users,
  Eye
} from 'lucide-react';

const API_BASE = '';

// Surligne la ponctuation-clé (guillemets, deux-points, cadratins) en rouge gras
// pour l'affichage du calque. N'altère PAS le texte réel envoyé à l'IA.
function highlightPunctuation(text) {
  const esc = (text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  // « » : — (cadratin) – (demi-cadratin) - (trait d'union : incises « dit-il »)
  return esc.replace(/[«»:—–\-]/g, (m) => `<span class="hl-mark">${m}</span>`) + '\n';
}

function App() {
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  // Réf. toujours à jour du projet sélectionné : sert à jeter les réponses
  // de refreshProject arrivées APRÈS un changement de projet (course async).
  const selectedProjectRef = useRef(null);
  const [projectDetails, setProjectDetails] = useState(null);
  const [isLoadingProject, setIsLoadingProject] = useState(false);
  const [voices, setVoices] = useState([]);
  const [voiceMapping, setVoiceMapping] = useState({});
  const [activeAudio, setActiveAudio] = useState(null);
  
  // Terminal logs state
  const [terminalLogs, setTerminalLogs] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const terminalEndRef = useRef(null);
  
  // Modals and inputs
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectType, setNewProjectType] = useState('novel');
  const [editingSegment, setEditingSegment] = useState(null);
  const [editFromQc, setEditFromQc] = useState(false); // éditeur ouvert depuis QC → pas de « Scinder »
  const [editText, setEditText] = useState('');
  const [editProfileId, setEditProfileId] = useState('');
  const [isSplitting, setIsSplitting] = useState(false);
  const [splitPart1, setSplitPart1] = useState('');
  const [splitPart2, setSplitPart2] = useState('');

  // QC States (natif : score auto par segment)
  const [qcScores, setQcScores] = useState({});
  const [qcScoresLoading, setQcScoresLoading] = useState({});
  const [qcRecomputeLoading, setQcRecomputeLoading] = useState(false);
  const [errorDetailMsg, setErrorDetailMsg] = useState(null);
  const [newCharacterName, setNewCharacterName] = useState('');

  // Multi-version generation states
  const [segmentVersions, setSegmentVersions] = useState({});
  const [versionSelectionSegment, setVersionSelectionSegment] = useState(null);
  const [versionScores, setVersionScores] = useState({});  // {v1: {score, duration}, ...}
  const [selectedVersion, setSelectedVersion] = useState('');
  const [playingVersion, _setPlayingVersion] = useState(null);
  const playingVersionRef = useRef(null);
  const setPlayingVersion = (v) => {
    _setPlayingVersion(v);
    playingVersionRef.current = v;
  };
  const [versionAutoplayActive, _setVersionAutoplayActive] = useState(false);
  const versionAutoplayActiveRef = useRef(false);
  const setVersionAutoplayActive = (val) => {
    _setVersionAutoplayActive(val);
    versionAutoplayActiveRef.current = val;
  };
  const [previewIsPlaying, setPreviewIsPlaying] = useState(false);
  const previewAudioRef = useRef(null);

  // Volume persistence states
  const [playerVolume, setPlayerVolume] = useState(() => {
    const saved = localStorage.getItem('playerVolume');
    return saved !== null ? parseFloat(saved) : 1.0;
  });
  const audioRef = useRef(null);

  // Drag and Drop Upload state
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);
  const isFr = navigator.language ? navigator.language.startsWith('fr') : true;
  
  // Generation configuration
  const [generationRange, setGenerationRange] = useState('');
  const [selectedVoiceFilter, setSelectedVoiceFilter] = useState('');
  const [mainNarratorVoice, setMainNarratorVoice] = useState('Narrator');
  
  // MP3 compilation configuration
  const [concatRange, setConcatRange] = useState('');
  const [concatFilename, setConcatFilename] = useState('');

  // Resizable terminal height state
  const [terminalHeight, setTerminalHeight] = useState(160);
  const [isDraggingTerminal, setIsDraggingTerminal] = useState(false);
  const [autoplayEnabled, setAutoplayEnabled] = useState(false);
  const [activeCharacterFilters, setActiveCharacterFilters] = useState([]);
  const [playedAudioTimes, setPlayedAudioTimes] = useState({});
  
  // Quality Center states (panneau QC ouvrant par la gauche)
  const [isQcOpen, setIsQcOpen] = useState(false);
  const [isQcExpanded, setIsQcExpanded] = useState(false);
  const [qcThresholds, setQcThresholds] = useState({}); // { PERSO: { "1":45, "2":50, ... } }
  const [qcThresholdsSaving, setQcThresholdsSaving] = useState(false);
  // True dès qu'une saisie de seuil n'est pas encore enregistrée → empêche le
  // poll de rafraîchissement (toutes les 2 s) d'écraser les valeurs en cours.
  const qcThresholdsDirtyRef = useRef(false);
  // Seuils par VOIX utilisée (romans mono/multi-voix). { voice_name: {bucket: val} }
  const [voiceThresholds, setVoiceThresholds] = useState({});
  const [voicesUsedMeta, setVoicesUsedMeta] = useState({}); // { voice_name: {voice_id} }
  const voiceThresholdsDirtyRef = useRef(false);
  const [qcContextMenu, setQcContextMenu] = useState(null); // { num, x, y } | null
  const [qcBatchVersions, setQcBatchVersions] = useState(1); // versions pour le batch QC
  const [manuallyValidated, setManuallyValidated] = useState({}); // { num: 'validating' | 'done' }
  const [qcSessionNewAudios, setQcSessionNewAudios] = useState({}); // { num: true }
  const [lastPlaySource, setLastPlaySource] = useState('central'); // 'central' or 'qc'
  const [qcAutoplayUseCentral, setQcAutoplayUseCentral] = useState(false);

  // IA & Rôles (roman à personnages) states
  const [isAiRolesOpen, setIsAiRolesOpen] = useState(false);
  const [aiRolesLoading, setAiRolesLoading] = useState(false);
  const [aiRolesChunks, setAiRolesChunks] = useState([]);
  const [aiRolesSource, setAiRolesSource] = useState(null);
  const [selectedAiChunk, setSelectedAiChunk] = useState(null); // { index, filename, text, analyzed_text }
  const [aiChunkEdit, setAiChunkEdit] = useState(''); // cadre AVANT éditable
  const aiBackdropRef = useRef(null); // calque de coloration du cadre AVANT
  const [aiAnalyzedEdit, setAiAnalyzedEdit] = useState(''); // cadre APRÈS éditable
  const [aiAnalyzedSaving, setAiAnalyzedSaving] = useState(false);
  const aiAfterRef = useRef(null); // textarea du cadre APRÈS
  const aiAfterBackdropRef = useRef(null); // calque de coloration du cadre APRÈS
  // Auto-complétion des noms de personnages (déclenchée en MAJUSCULES).
  const [aiSuggest, setAiSuggest] = useState({ open: false, items: [], index: 0, tokenStart: 0, top: 0, left: 0 });
  const [aiRange, setAiRange] = useState(''); // plage de chunks à analyser en lot
  // Analyse IA asynchrone : statuts { index: {status:'queued'|'running'|'done'|'error', ...} }
  // remontés par polling de l'orchestrator (insensible au reverse proxy).
  const [aiJobs, setAiJobs] = useState({});
  const aiPollRef = useRef(null);       // id de l'intervalle de polling
  const aiDoneHandledRef = useRef(new Set()); // indices dont la fin a déjà été traitée

  // Atelier de Normalisation states
  const [isNormalizerOpen, setIsNormalizerOpen] = useState(false);
  const [removePageNumbers, setRemovePageNumbers] = useState(true);
  const [pieceStyle, setPieceStyle] = useState('modern');
  const [playStyles, setPlayStyles] = useState([
    { id: 'modern', name: 'Moderne (Eva (dida) – dialogue)', pattern: '^__ACTORS__\\s*(?:\\(([^)]*)\\))?\\s*[-–\\—]\\s*(.*)$', system: true },
    { id: 'classic', name: 'Classique avec Point (JEANNE. dialogue)', pattern: '^__ACTORS__\\s*\\.\\s*(.*)$', system: true },
    { id: 'moliere', name: 'Alternance Simple (Dorine\\ndialogue)', pattern: '^\\s*__ACTORS__\\s*$', system: true }
  ]);
  const [showStyleEditor, setShowStyleEditor] = useState(false);
  const [editingStyle, setEditingStyle] = useState(null);
  const [styleEditorError, setStyleEditorError] = useState('');
  const [normalizerRawSample, setNormalizerRawSample] = useState(
    `Eva (parlant de sa tenue) – Ça ira, comme ça ?\nAlban – Mais oui.\nEva – Je me demandais si ce n'était pas un peu…\nAlban – Non, c'est discret... C'est passe-partout…\n\n3\n\nDorine\nC'est une conscience\nQue de vous laisser faire une telle alliance.`
  );
  const [normalizerPreviewText, setNormalizerPreviewText] = useState('');
  const [normalizerTopText, setNormalizerTopText] = useState('');
  const [normalizerBottomText, setNormalizerBottomText] = useState('');
  const [normalizerBaseFormattedText, setNormalizerBaseFormattedText] = useState('');
  const [normalizerLoading, setNormalizerLoading] = useState(false);
  const [normalizerStep, setNormalizerStep] = useState(1);
  const [parsedTextSample, setParsedTextSample] = useState('');
  const [chunkedTextSample, setChunkedTextSample] = useState('');
  const [parsedFilename, setParsedFilename] = useState('');
  const [chunkedFilename, setChunkedFilename] = useState('');
  
  const [normalizationRules, setNormalizationRules] = useState([]);
  const [newSearchRule, setNewSearchRule] = useState('');
  const [newReplaceRule, setNewReplaceRule] = useState('');

  
  const [normalizationPresets, setNormalizationPresets] = useState([]);
  const [selectedPresetName, setSelectedPresetName] = useState('');
  const [showSavePresetModal, setShowSavePresetModal] = useState(false);
  const [newPresetName, setNewPresetName] = useState('');
  
  // Scroll tracking state & Ref
  const [scrollTrackingActive, setScrollTrackingActive] = useState(() => {
    const saved = localStorage.getItem('scrollTrackingActive');
    return saved !== null ? saved === 'true' : true;
  });
  const segmentListContainerRef = useRef(null);

  // Active segment generation progress tracking
  const [activeSegmentProgress, setActiveSegmentProgress] = useState(0);
  const activeSegmentStartRef = useRef(null);
  // N° de tentative QC en cours (boucle de régénération QC), affiché « En cours (12) ».
  const [qcAttempt, setQcAttempt] = useState(null);
  // Meilleur score retenu jusqu'ici pendant la boucle → « En cours (12 - 22%) ».
  const [qcBestPct, setQcBestPct] = useState(null);
  // Vitesse TTS mesurée dynamiquement (ms/caractère), calibrée par le backend
  const [ttsSpeed, setTtsSpeed] = useState(null);
  const ttsSpeedRef = useRef(null);
  
  const eventSourceRef = useRef(null);
  const [activeAction, setActiveAction] = useState(null);

  // Client-side text normalization simulation for real-time Avant/Après preview
  const getMockNormalizedText = (rawText) => {
    if (!rawText) return '';
    const lines = rawText.split('\n');
    
    // Get list of uppercase actors for matching
    const actors = (projectDetails?.custom_characters && projectDetails.custom_characters.length > 0)
      ? projectDetails.custom_characters.map(a => a.toUpperCase())
      : ['EVA', 'ALBAN', 'JEANNE', 'SIMON', 'RALPH', 'DORINE', 'ORGON', 'DIDAS'];
      
    // 1. Strip and filter/remove page numbers if required
    const cleanedLines = [];
    for (let line of lines) {
      const stripped = line.trim();
      if (removePageNumbers) {
        if (/^\d+$/.test(stripped)) {
          continue;
        }
        if (/^\d+\.\s+.*$/.test(stripped)) {
          continue;
        }
      }
      cleanedLines.push(stripped);
    }

    // Helper to check if a line starts a new speech
    const startsNewSpeech = (sLine) => {
      if (!sLine) return false;
      if (pieceStyle === 'modern') {
        for (const actor of actors) {
          const escActor = actor.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
          const regex = new RegExp("^(" + escActor + ")\\s*(?:\\(([^)]*)\\))?\\s*[-–—]\\s*(.*)$", "i");
          if (regex.test(sLine)) {
            return true;
          }
        }
      } else if (pieceStyle === 'classic') {
        for (const actor of actors) {
          const escActor = actor.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
          const regex = new RegExp("^(" + escActor + ")\\s*\\.\\s*(.*)$", "i");
          if (regex.test(sLine)) {
            return true;
          }
        }
      } else if (pieceStyle === 'moliere') {
        if (actors.includes(sLine.toUpperCase())) {
          return true;
        }
      }
      return false;
    };

    // Merge consecutive non-empty lines belonging to the same block
    const merged = [];
    for (const line of cleanedLines) {
      if (!line) {
        merged.push('');
        continue;
      }
      
      if (merged.length === 0) {
        merged.push(line);
        continue;
      }
      
      const prev = merged[merged.length - 1];
      let canMerge = false;
      
      if (prev !== '') {
        if (!startsNewSpeech(line)) {
          if (pieceStyle === 'moliere') {
            if (actors.includes(prev.toUpperCase())) {
              canMerge = false;
            } else {
              canMerge = true;
            }
          } else {
            canMerge = true;
          }
        }
      }
      
      if (canMerge) {
        merged[merged.length - 1] = prev + ' ' + line;
      } else {
        merged.push(line);
      }
    }

    const normalizedLines = [];
    let i = 0;
    while (i < merged.length) {
      const line = merged[i];
      
      if (!line) {
        normalizedLines.push('');
        i++;
        continue;
      }
      
      let matched = false;
      if (pieceStyle === 'modern') {
        for (const actor of actors) {
          const escActor = actor.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
          const regex = new RegExp("^(" + escActor + ")\\s*(?:\\(([^)]*)\\))?\\s*[-–—]\\s*(.*)$", "i");
          const match = line.match(regex);
          if (match) {
            const [_, name, didas, dialogue] = match;
            if (normalizedLines.length > 0 && normalizedLines[normalizedLines.length - 1] !== '') {
              normalizedLines.push('');
            }
            normalizedLines.push(name.toUpperCase());
            normalizedLines.push('');
            if (didas) {
              normalizedLines.push(`(${didas.trim()}) ${dialogue.trim()}`);
            } else {
              normalizedLines.push(dialogue.trim());
            }
            normalizedLines.push('');
            matched = true;
            break;
          }
        }
      } else if (pieceStyle === 'classic') {
        for (const actor of actors) {
          const escActor = actor.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
          const regex = new RegExp("^(" + escActor + ")\\s*\\.\\s*(.*)$", "i");
          const match = line.match(regex);
          if (match) {
            const [_, name, dialogue] = match;
            if (normalizedLines.length > 0 && normalizedLines[normalizedLines.length - 1] !== '') {
              normalizedLines.push('');
            }
            normalizedLines.push(name.toUpperCase());
            normalizedLines.push('');
            normalizedLines.push(dialogue.trim());
            normalizedLines.push('');
            matched = true;
            break;
          }
        }
      } else if (pieceStyle === 'moliere') {
        const upperLine = line.toUpperCase();
        if (actors.includes(upperLine)) {
          if (normalizedLines.length > 0 && normalizedLines[normalizedLines.length - 1] !== '') {
            normalizedLines.push('');
          }
          normalizedLines.push(upperLine);
          normalizedLines.push('');
          matched = true;
        }
      }
      
      if (!matched) {
        normalizedLines.push(line);
      }
      i++;
    }
    
    // Collapse consecutive multiple empty lines
    const result = [];
    let prevEmpty = false;
    for (const l of normalizedLines) {
      if (l === '') {
        if (!prevEmpty) {
          result.push('');
        }
        prevEmpty = true;
      } else {
        result.push(l);
        prevEmpty = false;
      }
    }
    
    return result.join('\n');
  };

  const loadNormalizationPresets = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/presets/normalization`);
      if (res.ok) {
        const data = await res.json();
        setNormalizationPresets(data);
      }
    } catch (e) {
      console.error("Error loading normalization presets:", e);
    }
  };

  const applyPreset = async (preset) => {
    if (!selectedProject || !normalizerRawSample) return;
    setNormalizerLoading(true);
    try {
      const newStyle = preset.piece_style;
      const newRemovePage = preset.remove_page_numbers;
      const newRules = preset.normalization_rules;

      // Update local states
      setPieceStyle(newStyle);
      setRemovePageNumbers(newRemovePage);
      setNormalizationRules(newRules);

      await refreshPreview(newRules, newStyle, newRemovePage);
    } catch (e) {
      console.error("Error applying preset:", e);
    } finally {
      setNormalizerLoading(false);
    }
  };

  const handleSavePreset = async () => {
    if (!newPresetName.trim()) return;
    try {
      const presetData = {
        piece_style: getPieceStylePayload(pieceStyle),
        remove_page_numbers: removePageNumbers,
        normalization_rules: normalizationRules
      };
      const res = await fetch(`${API_BASE}/api/presets/normalization/${encodeURIComponent(newPresetName.trim())}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(presetData)
      });
      if (res.ok) {
        setNewPresetName('');
        setShowSavePresetModal(false);
        setSelectedPresetName(newPresetName.trim());
        await loadNormalizationPresets();
      } else {
        alert(isFr ? "Erreur lors de la sauvegarde du preset" : "Error saving preset");
      }
    } catch (e) {
      console.error("Error saving preset:", e);
    }
  };

  const handleDeletePreset = async (name, e) => {
    if (e) e.stopPropagation();
    if (!confirm(isFr ? `Supprimer le preset "${name}" ?` : `Delete preset "${name}"?`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/presets/normalization/${encodeURIComponent(name)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        await loadNormalizationPresets();
        if (selectedPresetName === name) {
          setSelectedPresetName('');
        }
      }
    } catch (e) {
      console.error("Error deleting preset:", e);
    }
  };

  const getPieceStylePayload = (styleId) => {
    const styleObj = playStyles.find(s => s.id === styleId);
    if (!styleObj) return styleId;
    if (styleObj.system) {
      return styleObj.id;
    }
    return {
      id: styleObj.id,
      name: styleObj.name,
      pattern: styleObj.pattern,
      system: false
    };
  };

  const handleSavePlayStyle = async () => {
    if (!editingStyle) return;
    if (!editingStyle.name.trim()) {
      setStyleEditorError(isFr ? "Le nom est obligatoire." : "Name is required.");
      return;
    }
    if (!editingStyle.pattern.trim()) {
      setStyleEditorError(isFr ? "Le pattern regex est obligatoire." : "Regex pattern is required.");
      return;
    }
    if (!editingStyle.pattern.includes('__ACTORS__')) {
      setStyleEditorError(isFr ? "Le pattern doit contenir __ACTORS__." : "The pattern must contain __ACTORS__.");
      return;
    }
    try {
      const dummy = editingStyle.pattern.replace('__ACTORS__', 'DUMMY');
      new RegExp(dummy);
    } catch (e) {
      setStyleEditorError((isFr ? "Expression régulière invalide : " : "Invalid regular expression: ") + e.message);
      return;
    }

    try {
      const currentCustoms = playStyles.filter(s => !s.system);
      let updatedCustoms;
      
      const existsIdx = currentCustoms.findIndex(s => s.id === editingStyle.id);
      if (existsIdx >= 0) {
        updatedCustoms = [...currentCustoms];
        updatedCustoms[existsIdx] = { ...editingStyle, system: false };
      } else {
        updatedCustoms = [...currentCustoms, { ...editingStyle, system: false }];
      }

      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/custom_play_styles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ custom_play_styles: updatedCustoms })
      });

      if (res.ok) {
        const systemStyles = playStyles.filter(s => s.system);
        const nextStyles = [...systemStyles, ...updatedCustoms];
        setPlayStyles(nextStyles);
        setPieceStyle(editingStyle.id);
        setShowStyleEditor(false);
        setEditingStyle(null);
        setStyleEditorError('');
        
        // Use nextStyles instead of relying on state batch update
        const rulesList = normalizationRules;
        const styleVal = editingStyle.id;
        const removePageVal = removePageNumbers;
        const rawText = normalizerRawSample;
        if (selectedProject && rawText) {
          setNormalizerLoading(true);
          const activeRules = rulesList.filter(r => r.active);
          const styleObj = nextStyles.find(s => s.id === styleVal);
          const stylePayload = styleObj && !styleObj.system ? { id: styleObj.id, name: styleObj.name, pattern: styleObj.pattern, system: false } : styleVal;
          const previewRes = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalize/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              text: rawText,
              remove_page_numbers: removePageVal,
              piece_style: stylePayload,
              normalization_rules: activeRules
            })
          });
          if (previewRes.ok) {
            const previewData = await previewRes.json();
            const v1 = previewData.normalized_text;
            setNormalizerBaseFormattedText(v1);
            recalculateLocalCascade(rulesList, newSearchRule, newReplaceRule, v1);
          }
          setNormalizerLoading(false);
        }
      } else {
        const err = await res.json();
        setStyleEditorError(err.detail || "Error saving style");
      }
    } catch (e) {
      console.error(e);
      setStyleEditorError(isFr ? "Erreur réseau lors de la sauvegarde." : "Network error while saving.");
    }
  };

  const handleDeletePlayStyle = async (styleId) => {
    if (!window.confirm(isFr ? "Êtes-vous sûr de vouloir supprimer ce style ?" : "Are you sure you want to delete this style?")) {
      return;
    }
    try {
      const remainingCustoms = playStyles.filter(s => !s.system && s.id !== styleId);
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/custom_play_styles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ custom_play_styles: remainingCustoms })
      });

      if (res.ok) {
        const systemStyles = playStyles.filter(s => s.system);
        setPlayStyles([...systemStyles, ...remainingCustoms]);
        if (pieceStyle === styleId) {
          setPieceStyle('modern');
          await refreshPreview(normalizationRules, 'modern', removePageNumbers);
        }
      } else {
        const err = await res.json();
        alert((isFr ? "Erreur lors de la suppression : " : "Error deleting style: ") + (err.detail || ""));
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? "Erreur réseau" : "Network error");
    }
  };

  const refreshPreview = async (rulesList = normalizationRules, styleVal = pieceStyle, removePageVal = removePageNumbers, rawText = normalizerRawSample) => {
    if (!selectedProject || !rawText) return;
    setNormalizerLoading(true);
    try {
      const activeRules = rulesList.filter(r => r.active);
      const previewRes = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalize/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: rawText,
          remove_page_numbers: removePageVal,
          piece_style: getPieceStylePayload(styleVal),
          normalization_rules: activeRules
        })
      });
      
      if (previewRes.ok) {
        const previewData = await previewRes.json();
        const v1 = previewData.normalized_text;
        setNormalizerBaseFormattedText(v1);
        recalculateLocalCascade(rulesList, newSearchRule, newReplaceRule, v1);
      }
    } catch (e) {
      console.error("Error refreshing normalization preview:", e);
    } finally {
      setNormalizerLoading(false);
    }
  };

  const handleSaveSourceText = async () => {
    if (!selectedProject || !normalizerTopText) return;
    setNormalizerLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/source_text`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: normalizerTopText })
      });
      if (res.ok) {
        // WYSIWYG : on fige EXACTEMENT ce qui est affiché/édité. On NE rejoue PAS
        // les regex ensuite (sinon une règle — ex. chapitre romain — re-matcherait
        // « C D » et écraserait une correction manuelle). Le texte sauvé devient
        // la nouvelle base ; les futures règles s'appliqueront dessus.
        setNormalizerRawSample(normalizerTopText);
        setNormalizerBaseFormattedText(normalizerTopText);
        setNormalizerBottomText(normalizerTopText);
        alert(isFr ? "Fichier source enregistré avec succès !" : "Source file saved successfully!");
      } else {
        const err = await res.json();
        alert((isFr ? "Erreur de sauvegarde : " : "Save error: ") + (err.detail || ""));
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? "Erreur réseau lors de la sauvegarde" : "Network error while saving");
    } finally {
      setNormalizerLoading(false);
    }
  };

  const loadSourceTextSample = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/source_text_sample`);
      if (res.ok) {
        const data = await res.json();
        if (data.text) {
          const rawText = data.raw_text || data.text;
          setNormalizerRawSample(rawText);
          await refreshPreview(normalizationRules, pieceStyle, removePageNumbers, rawText);
        } else {
          setNormalizerRawSample('');
          setNormalizerTopText('');
          setNormalizerBottomText('');
          setNormalizerBaseFormattedText('');
        }
      }
    } catch (e) {
      console.error("Error loading source text sample:", e);
    } finally {
      setNormalizerLoading(false);
    }
  };

  const triggerReformat = async (newStyle, newRemovePage) => {
    await refreshPreview(normalizationRules, newStyle, newRemovePage);
  };

  const applyJsRule = (text, rule) => {
    if (!text || !rule || !rule.search) return text;
    const search = rule.search;
    const replace = rule.replace || "";
    
    // Convert literal \n in replacement text to real newlines
    const cleanReplace = replace.replace(/\\n/g, '\n');
    
    let regex = null;
    if (search.startsWith('/') && search.lastIndexOf('/') > 0) {
      const lastSlash = search.lastIndexOf('/');
      const pattern = search.substring(1, lastSlash);
      const flags = search.substring(lastSlash + 1);
      try {
        const cleanFlags = flags.includes('g') ? flags : flags + 'g';
        regex = new RegExp(pattern, cleanFlags);
      } catch (e) {
        console.error("Invalid regex rule:", search, e);
        return text;
      }
    } else {
      // Try to compile as a regex first; if it fails (invalid syntax), treat as literal
      try {
        regex = new RegExp(search, 'g');
      } catch (e) {
        const escapedSearch = search.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
        regex = new RegExp(escapedSearch, 'g');
      }
    }

    // Apply the regex line by line to align with backend behavior and support ^/$ matches natively
    const lines = text.split('\n');
    const processedLines = lines.map(line => line.replace(regex, cleanReplace));
    return processedLines.join('\n');
  };

  const recalculateLocalCascade = (rulesList, testSearch = '', testReplace = '', baseText = null) => {
    let currentText = baseText !== null ? baseText : (normalizerBaseFormattedText || "");
    
    // We do NOT apply active rules list locally because they are already applied by the backend in the base text.
    setNormalizerTopText(currentText);
    
    if (testSearch && testSearch.trim()) {
      const testRule = {
        id: 'rule_temp',
        search: testSearch,
        replace: testReplace,
        active: true
      };
      const testedText = applyJsRule(currentText, testRule);
      setNormalizerBottomText(testedText);
    } else {
      setNormalizerBottomText(currentText);
    }
  };

  const handleAddRule = async () => {
    if (!newSearchRule.trim()) return;
    const newRule = {
      id: 'rule_' + Date.now(),
      search: newSearchRule,
      replace: newReplaceRule,
      active: true
    };
    const updatedRules = [...normalizationRules, newRule];
    setNormalizationRules(updatedRules);
    
    setNewSearchRule('');
    setNewReplaceRule('');
    
    await refreshPreview(updatedRules);
    
    if (selectedProject) {
      try {
        await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalization_rules`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ normalization_rules: updatedRules })
        });
      } catch (e) {
        console.error("Error saving normalization rules:", e);
      }
    }
  };

  const handleDeleteRule = async (id) => {
    const updatedRules = normalizationRules.filter(r => r.id !== id);
    setNormalizationRules(updatedRules);
    
    await refreshPreview(updatedRules);
    
    if (selectedProject) {
      try {
        await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalization_rules`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ normalization_rules: updatedRules })
        });
      } catch (e) {
        console.error("Error deleting normalization rule:", e);
      }
    }
  };

  const handleToggleRule = async (id) => {
    const updatedRules = normalizationRules.map(r => r.id === id ? { ...r, active: !r.active } : r);
    setNormalizationRules(updatedRules);
    
    await refreshPreview(updatedRules);
    
    if (selectedProject) {
      try {
        await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalization_rules`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ normalization_rules: updatedRules })
        });
      } catch (e) {
        console.error("Error toggling normalization rule:", e);
      }
    }
  };

  const handleTestNormalizationRules = () => {
    recalculateLocalCascade(normalizationRules, newSearchRule, newReplaceRule);
  };

  const handleApplyNormalizationRules = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalize/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remove_page_numbers: removePageNumbers,
          piece_style: getPieceStylePayload(pieceStyle),
          text: normalizerTopText,
          normalization_rules: normalizationRules
        })
      });
      if (res.ok) {
        alert(isFr ? "Règles appliquées avec succès ! Le fichier normalisé a été créé." : "Rules applied successfully! The normalized file has been created.");
        setIsNormalizerOpen(false);
        await refreshProject(selectedProject);
      } else {
        const errData = await res.json();
        alert((isFr ? "Erreur lors de l'application : " : "Error applying rules: ") + (errData.detail || ""));
      }
    } catch (e) {
      console.error("Error applying normalizer rules:", e);
      alert(isFr ? "Erreur réseau lors de l'application" : "Network error applying rules");
    } finally {
      setNormalizerLoading(false);
    }
  };

  const handleNovelNormalizeAndSplit = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    setTerminalLogs(prev => [...prev, `[INFO] Normalisation du roman + création des segments...`]);
    try {
      const resApply = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalize/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remove_page_numbers: removePageNumbers,
          piece_style: 'none',
          text: normalizerTopText,
          normalization_rules: normalizationRules,
          overwrite_source: true,
        })
      });
      if (!resApply.ok) { const e = await resApply.json(); throw new Error(e.detail || "Erreur de normalisation"); }
      setTerminalLogs(prev => [...prev, `[INFO] Texte source normalisé. Découpage en segments...`]);

      const resSplit = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/run_tool_sync?action=split`, { method: 'POST' });
      if (!resSplit.ok) { const e = await resSplit.json(); throw new Error(e.detail || "Erreur de découpage"); }

      setTerminalLogs(prev => [...prev, `[OK] Segments créés à partir du texte normalisé.`]);
      setIsNormalizerOpen(false);
      await refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
    } finally {
      setNormalizerLoading(false);
    }
  };

  const handleNovelNormalizeAndGoToAiRoles = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    setTerminalLogs(prev => [...prev, `[INFO] Normalisation du roman...`]);
    try {
      const resApply = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalize/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remove_page_numbers: removePageNumbers,
          piece_style: 'none',
          text: normalizerTopText,      // texte déjà normalisé (règles + n° page)
          normalization_rules: normalizationRules,
          overwrite_source: true,       // écrit dans le txt source (pas de _formated)
        })
      });
      if (!resApply.ok) { const e = await resApply.json(); throw new Error(e.detail || "Erreur de normalisation"); }
      setTerminalLogs(prev => [...prev, `[OK] Texte source normalisé. Passage à l'étape IA & Rôles...`]);
      setIsNormalizerOpen(false);
      await refreshProject(selectedProject);

      // Ouvrir automatiquement l'attribution des rôles par l'IA
      setIsAiRolesOpen(true);
      setSelectedAiChunk(null);
      setAiRolesSource(projectDetails?.source_txt_file || null);
      setAiRolesLoading(true);
      try {
        let res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks`);
        if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
        let data = await res.json();
        if (!data.chunks || data.chunks.length === 0) {
          // Aucun chunk encore : premier découpage.
          setTerminalLogs(prev => [...prev, `[INFO] IA & Rôles : découpage initial en chunks de 1500-2000 caractères...`]);
          res = await fetch(
            `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunk?min_chars=1500&max_chars=2000`,
            { method: 'POST' }
          );
          if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur de découpage en chunks"); }
          res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks`);
          data = await res.json();
        }
        setAiRolesChunks(data.chunks || []);
      } catch (err) {
        console.error(err);
        setTerminalLogs(prev => [...prev, `[ERREUR] ${err.message}`]);
      } finally {
        setAiRolesLoading(false);
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
    } finally {
      setNormalizerLoading(false);
    }
  };

  // Bascule le drawer IA & Rôles (ouvre/ferme, comme Quality Center).
  // À l'OUVERTURE : on ne fait que LISTER les chunks existants (on ne re-découpe
  // JAMAIS automatiquement, pour ne pas écraser le travail déjà analysé). On ne
  // découpe que s'il n'existe encore aucun chunk.
  const handleToggleAiRoles = async () => {
    if (isAiRolesOpen) { setIsAiRolesOpen(false); return; }
    if (!selectedProject) return;
    setIsAiRolesOpen(true);
    setSelectedAiChunk(null);
    setAiRolesSource(projectDetails?.source_txt_file || null);
    setAiRolesLoading(true);
    try {
      let res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks`);
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
      let data = await res.json();
      if (!data.chunks || data.chunks.length === 0) {
        // Aucun chunk encore : premier découpage.
        setTerminalLogs(prev => [...prev, `[INFO] IA & Rôles : découpage initial en chunks de 1500-2000 caractères...`]);
        res = await fetch(
          `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunk?min_chars=1500&max_chars=2000`,
          { method: 'POST' }
        );
        if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur de découpage"); }
        data = await res.json();
        setAiRolesSource(data.source_file || null);
        setTerminalLogs(prev => [...prev, `[OK] ${data.count} chunks générés.`]);
      }
      setAiRolesChunks(data.chunks || []);
      // Reflète d'éventuelles analyses déjà en cours (lancées avant, ou depuis
      // un autre onglet) et relance le suivi si nécessaire.
      startAiPolling();
    } catch (e) {
      console.error(e);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
    } finally {
      setAiRolesLoading(false);
    }
  };

  // Assemble tous les chunks validés en <projet>_parsed.txt (format théâtre),
  // prêt à être chunké/splitté en segments pour le tableau central.
  const handleConcatValidated = async () => {
    if (!selectedProject) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/concat`,
        { method: 'POST' }
      );
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
      const data = await res.json();
      setTerminalLogs(prev => [...prev, `[OK] Assemblé : ${data.file} (${data.included}/${data.total} chunks validés).`]);
      if ((data.missing || []).length) {
        setTerminalLogs(prev => [...prev, `[ATTENTION] ${data.missing.length} chunk(s) non validé(s) et donc EXCLUS : ${data.missing.slice(0, 40).join(', ')}${data.missing.length > 40 ? '…' : ''}.`]);
        alert(isFr
          ? `Assemblé, mais ${data.missing.length} chunk(s) ne sont pas encore validés et ont été exclus. Le livre est incomplet.`
          : `Assembled, but ${data.missing.length} chunk(s) are not validated yet and were excluded. The book is incomplete.`);
      } else {
        alert(isFr ? `Livre assemblé : ${data.file} (${data.included} chunks).` : `Book assembled: ${data.file} (${data.included} chunks).`);
      }
      setIsAiRolesOpen(false);
      refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
    }
  };

  // --- Auto-complétion des noms de personnages dans le cadre APRÈS ---
  // Police monospace (Courier 13px, line-height 1.5, padding 12) => on peut
  // approximer la position du curseur pour ancrer la liste dessous.
  const AI_CHAR_W = 7.83, AI_LINE_H = 19.5, AI_PAD = 12;

  const updateAiSuggestions = (el) => {
    const val = el.value;
    const caret = el.selectionStart;
    const before = val.slice(0, caret);
    // Token en cours = suite de MAJUSCULES (+ espaces/tirets/apostrophes) collée au curseur.
    const m = before.match(/[A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\- ]*$/);
    const chars = projectDetails?.custom_characters || [];
    if (!m || m[0].trim().length < 2) {
      setAiSuggest(s => (s.open ? { ...s, open: false } : s));
      return;
    }
    const token = m[0];
    const up = token.toUpperCase().trim();
    const items = chars.filter(c => c.toUpperCase().startsWith(up) && c.toUpperCase() !== up).slice(0, 8);
    if (!items.length) {
      setAiSuggest(s => (s.open ? { ...s, open: false } : s));
      return;
    }
    const lastNl = before.lastIndexOf('\n');
    const col = before.length - (lastNl + 1);
    const line = (before.match(/\n/g) || []).length;
    const top = AI_PAD + (line + 1) * AI_LINE_H - el.scrollTop;
    const left = Math.max(4, AI_PAD + col * AI_CHAR_W - el.scrollLeft);
    setAiSuggest({ open: true, items, index: 0, tokenStart: caret - token.length, top, left });
  };

  const acceptAiSuggestion = (name) => {
    const el = aiAfterRef.current;
    if (!el) return;
    const caret = el.selectionStart;
    const start = aiSuggest.tokenStart;
    const newVal = aiAnalyzedEdit.slice(0, start) + name + aiAnalyzedEdit.slice(caret);
    setAiAnalyzedEdit(newVal);
    setAiSuggest(s => ({ ...s, open: false }));
    requestAnimationFrame(() => {
      const p = start + name.length;
      el.focus();
      el.setSelectionRange(p, p);
    });
  };

  const onAiAfterKeyDown = (e) => {
    if (!aiSuggest.open || !aiSuggest.items.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setAiSuggest(s => ({ ...s, index: (s.index + 1) % s.items.length }));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setAiSuggest(s => ({ ...s, index: (s.index - 1 + s.items.length) % s.items.length }));
    } else if (e.key === 'Enter') {
      // ENTRÉE valide la suggestion (et NON Tab, inutile sur le web).
      e.preventDefault();
      acceptAiSuggestion(aiSuggest.items[aiSuggest.index]);
    } else if (e.key === 'Escape') {
      setAiSuggest(s => ({ ...s, open: false }));
    }
  };

  // Re-découpage EXPLICITE (efface les chunks ET les analyses) — sur confirmation.
  const handleRechunkAiRoles = async () => {
    if (!selectedProject) return;
    const msg = isFr
      ? "Re-découper le roman ? Cela EFFACERA tous les chunks ET les analyses (_analyzed) déjà enregistrées. Continuer ?"
      : "Re-chunk the novel? This will ERASE all chunks AND saved analyses. Continue?";
    if (!window.confirm(msg)) return;
    setSelectedAiChunk(null);
    setAiRolesLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunk?min_chars=1500&max_chars=2000`,
        { method: 'POST' }
      );
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur de découpage"); }
      const data = await res.json();
      setAiRolesChunks(data.chunks || []);
      setAiRolesSource(data.source_file || null);
      setTerminalLogs(prev => [...prev, `[OK] Re-découpage : ${data.count} chunks.`]);
      refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
    } finally {
      setAiRolesLoading(false);
    }
  };

  const handleSelectAiChunk = async (index) => {
    if (!selectedProject) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks/${index}`
      );
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur de chargement"); }
      const data = await res.json();
      setSelectedAiChunk(data);
      setAiChunkEdit(data.text || '');
      setAiAnalyzedEdit(data.analyzed_text || '');
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
    }
  };

  // Enregistre le contenu (éventuellement corrigé) du cadre APRÈS dans
  // chunk_NNNNN_analyzed.txt et resurface les personnages détectés.
  const handleSaveAnalyzed = async (index) => {
    if (!selectedProject) return;
    setAiAnalyzedSaving(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks/${index}/analyzed`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: aiAnalyzedEdit })
        }
      );
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
      const data = await res.json();
      setSelectedAiChunk(prev => prev && prev.index === index
        ? { ...prev, analyzed: true, analyzed_filename: data.analyzed_filename, analyzed_text: data.analyzed_text }
        : prev);
      setAiRolesChunks(prev => prev.map(c => c.index === index ? { ...c, analyzed: true } : c));
      setTerminalLogs(prev => [...prev, `[OK] ${data.analyzed_filename} enregistré.`]);
      refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
    } finally {
      setAiAnalyzedSaving(false);
    }
  };

  // Marque le chunk courant comme 100% NARRATEUR (sans IA) : enregistre le texte
  // (éventuellement corrigé dans le cadre AVANT), crée chunk_NNNNN_analyzed.txt
  // et l'affiche dans le cadre APRÈS.
  const handleMarkChunkNarrator = async (index) => {
    if (!selectedProject) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks/${index}/narrator`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: aiChunkEdit })
        }
      );
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
      const data = await res.json();
      // Met à jour l'aperçu (bloc analysé) + le statut dans la liste de droite.
      setSelectedAiChunk(prev => prev && prev.index === index
        ? { ...prev, text: aiChunkEdit, analyzed: true, analyzed_filename: data.analyzed_filename, analyzed_text: data.analyzed_text }
        : prev);
      setAiAnalyzedEdit(data.analyzed_text || '');
      setAiRolesChunks(prev => prev.map(c => c.index === index ? { ...c, analyzed: true } : c));
      // NARRATEUR est désormais "vu" côté projet (rafraîchit la colonne de gauche).
      refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
    }
  };

  // Polling des statuts d'analyse IA (petites requêtes → insensible au reverse
  // proxy). Se relance seul tant qu'il reste des jobs 'queued'/'running'.
  const pollAiStatus = async () => {
    if (!selectedProjectRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProjectRef.current)}/ai_roles/analyze_status`);
      if (!res.ok) return;
      const data = await res.json();
      const jobs = data.jobs || {};
      setAiJobs(jobs);

      for (const [k, job] of Object.entries(jobs)) {
        const idx = parseInt(k, 10);
        if (job.status === 'done' && !aiDoneHandledRef.current.has(idx)) {
          aiDoneHandledRef.current.add(idx);
          setAiRolesChunks(prev => prev.map(c => c.index === idx ? { ...c, analyzed: true } : c));
          const noms = (job.speakers || []).join(', ') || 'NARRATEUR uniquement';
          setTerminalLogs(prev => [...prev, `[OK] Chunk ${idx} analysé. Locuteurs : ${noms}.`]);
          if ((job.added_characters || []).length) {
            setTerminalLogs(prev => [...prev, `[INFO] Nouveaux personnages : ${job.added_characters.join(', ')}.`]);
          }
          // Si c'est le chunk affiché, recharge son contenu analysé (cadre APRÈS).
          setSelectedAiChunk(prev => {
            if (prev && prev.index === idx) handleSelectAiChunk(idx);
            return prev;
          });
        } else if (job.status === 'error' && !aiDoneHandledRef.current.has(idx)) {
          aiDoneHandledRef.current.add(idx);
          setTerminalLogs(prev => [...prev, `[ERREUR] Chunk ${idx} : ${job.error || 'analyse échouée'}`]);
        }
      }

      if ((data.active || 0) === 0) {
        if (aiPollRef.current) { clearInterval(aiPollRef.current); aiPollRef.current = null; }
        refreshProject(selectedProjectRef.current); // remonte les personnages détectés
      }
    } catch (e) {
      console.error('poll ai status', e);
    }
  };

  const startAiPolling = () => {
    if (aiPollRef.current) return;
    aiPollRef.current = setInterval(pollAiStatus, 2500);
    pollAiStatus();
  };

  // Reset du suivi d'analyse IA au changement de projet.
  useEffect(() => {
    setAiJobs({});
    aiDoneHandledRef.current = new Set();
    if (aiPollRef.current) { clearInterval(aiPollRef.current); aiPollRef.current = null; }
  }, [selectedProject]);

  // Nettoyage de l'intervalle de polling au démontage.
  useEffect(() => () => { if (aiPollRef.current) clearInterval(aiPollRef.current); }, []);

  // Analyse IA du chunk courant : ENQUEUE côté serveur (asynchrone), puis suivi
  // par polling. La requête HTTP est instantanée → aucun timeout de proxy.
  const handleAnalyzeChunkAI = async (index) => {
    if (!selectedProject) return;
    aiDoneHandledRef.current.delete(index);
    setTerminalLogs(prev => [...prev, `[INFO] Analyse IA du chunk ${index} mise en file...`]);
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks/${index}/analyze`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: aiChunkEdit }) }
      );
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
      setAiJobs(prev => ({ ...prev, [index]: { status: 'queued' } }));
      startAiPolling();
    } catch (e) {
      console.error(e);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
      alert(isFr ? `Analyse IA impossible : ${e.message}` : `AI analysis failed: ${e.message}`);
    }
  };

  // Analyse par LOT : "1-10, 15, 20-30" -> analyse IA de chaque chunk de la plage.
  const parseChunkRange = (str, max) => {
    const out = new Set();
    (str || '').split(',').forEach(part => {
      part = part.trim();
      if (!part) return;
      if (part.includes('-')) {
        const [a, b] = part.split('-').map(s => parseInt(s.trim(), 10));
        if (!isNaN(a) && !isNaN(b)) {
          for (let i = Math.min(a, b); i <= Math.max(a, b); i++) out.add(i);
        }
      } else {
        const n = parseInt(part, 10);
        if (!isNaN(n)) out.add(n);
      }
    });
    return [...out].filter(i => i >= 1 && i <= max).sort((x, y) => x - y);
  };

  const handleAnalyzeRange = async () => {
    if (!selectedProject) return;
    const indices = parseChunkRange(aiRange, aiRolesChunks.length);
    if (!indices.length) {
      alert(isFr ? "Plage invalide. Ex : 1-10, 15, 20-30" : "Invalid range. E.g. 1-10, 15, 20-30");
      return;
    }
    setTerminalLogs(prev => [...prev, `[INFO] Analyse IA par lot mise en file : chunks ${indices.join(', ')} (${indices.length}).`]);
    for (const idx of indices) {
      aiDoneHandledRef.current.delete(idx);
      try {
        const res = await fetch(
          `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/ai_roles/chunks/${idx}/analyze`,
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) }
        );
        if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Erreur"); }
        setAiJobs(prev => ({ ...prev, [idx]: { status: 'queued' } }));
      } catch (e) {
        setTerminalLogs(prev => [...prev, `[ERREUR] Mise en file chunk ${idx} : ${e.message}`]);
      }
    }
    startAiPolling();
  };

  const handleNextStepFromStep1 = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    setTerminalLogs(prev => [...prev, `[INFO] Étape 1 : Application des règles de normalisation...`]);
    try {
      // 1. Apply normalization (writes _formated.txt)
      const resApply = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/normalize/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remove_page_numbers: removePageNumbers,
          piece_style: getPieceStylePayload(pieceStyle),
          text: normalizerTopText,
          normalization_rules: normalizationRules
        })
      });
      
      if (!resApply.ok) {
        const errData = await resApply.json();
        throw new Error(errData.detail || "Erreur lors de la normalisation");
      }
      
      const applyData = await resApply.json();
      setTerminalLogs(prev => [...prev, `[INFO] Fichier normalisé créé : ${applyData.filename}`]);
      setTerminalLogs(prev => [...prev, `[INFO] Étape 2 : Lancement du parser de théâtre...`]);
      
      // 2. Run parse_theatre synchronously
      const resParse = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/run_tool_sync?action=parse_theatre`, {
        method: 'POST'
      });
      
      if (!resParse.ok) {
        const errData = await resParse.json();
        throw new Error(errData.detail || "Erreur lors du parsing");
      }
      
      const parseData = await resParse.json();
      setTerminalLogs(prev => [...prev, `[INFO] Parsing réussi avec succès.`]);
      
      // 3. Load the parsed file sample
      const resSample = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/file_sample?file_type=parsed`);
      if (resSample.ok) {
        const sampleData = await resSample.json();
        setParsedTextSample(sampleData.text);
        setParsedFilename(sampleData.filename);
      }
      
      // 4. Move to step 2
      setNormalizerStep(2);
      await refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
    } finally {
      setNormalizerLoading(false);
    }
  };

  const handleNextStepFromStep2 = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    setTerminalLogs(prev => [...prev, `[INFO] Étape 2 : Lancement du découpeur de répliques (chunker)...`]);
    try {
      // 1. Run chunker synchronously
      const resChunk = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/run_tool_sync?action=chunk_theatre`, {
        method: 'POST'
      });
      
      if (!resChunk.ok) {
        const errData = await resChunk.json();
        throw new Error(errData.detail || "Erreur lors du chunking");
      }
      
      setTerminalLogs(prev => [...prev, `[INFO] Chunking réussi.`]);
      
      // 2. Load the chunked file sample
      const resSample = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/file_sample?file_type=chunked`);
      if (resSample.ok) {
        const sampleData = await resSample.json();
        setChunkedTextSample(sampleData.text);
        setChunkedFilename(sampleData.filename);
      }
      
      // 3. Move to step 3
      setNormalizerStep(3);
      await refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
    } finally {
      setNormalizerLoading(false);
    }
  };

  const handleFinishFromStep3 = async () => {
    if (!selectedProject) return;
    setNormalizerLoading(true);
    setTerminalLogs(prev => [...prev, `[INFO] Étape 3 : Création des segments finaux...`]);
    try {
      // 1. Run split/segmentation tool
      const resSplit = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/run_tool_sync?action=split`, {
        method: 'POST'
      });
      
      if (!resSplit.ok) {
        const errData = await resSplit.json();
        throw new Error(errData.detail || "Erreur lors de la création des segments");
      }
      
      setTerminalLogs(prev => [...prev, `[INFO] Création des segments terminée.`]);
      alert(isFr ? "Segments créés avec succès !" : "Segments created successfully!");
      
      // 2. Exit normalizer and refresh project
      setIsNormalizerOpen(false);
      await refreshProject(selectedProject);
    } catch (e) {
      console.error(e);
      alert(isFr ? `Erreur : ${e.message}` : `Error: ${e.message}`);
      setTerminalLogs(prev => [...prev, `[ERREUR] ${e.message}`]);
    } finally {
      setNormalizerLoading(false);
    }
  };

  // Automatically load project sample when opening drawer
  useEffect(() => {
    if (isNormalizerOpen) {
      setNormalizerStep(1);
      loadSourceTextSample();
      loadNormalizationPresets();
    }
  }, [isNormalizerOpen, selectedProject]);

  // Client-side quick preview fallback if typing offline
  useEffect(() => {
    if (!selectedProject) {
      setNormalizerBottomText(getMockNormalizedText(normalizerTopText));
    }
  }, [normalizerTopText, removePageNumbers, pieceStyle, selectedProject]);


  const handleMouseDown = (e) => {
    e.preventDefault();
    setIsDraggingTerminal(true);
    const startY = e.clientY;
    const startHeight = terminalHeight;

    const handleMouseMove = (moveEvent) => {
      const deltaY = startY - moveEvent.clientY;
      const newHeight = Math.max(100, Math.min(600, startHeight + deltaY));
      setTerminalHeight(newHeight);
    };

    const handleMouseUp = () => {
      setIsDraggingTerminal(false);
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  };

  useEffect(() => {
    fetchProjects();
    fetchVoices();
  }, []);

  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [terminalLogs]);

  // Poll project details every 2 seconds while the generation is running
  useEffect(() => {
    let intervalId = null;
    if (isRunning && selectedProject) {
      intervalId = setInterval(() => {
        refreshProject(selectedProject);
      }, 2000);
    }
    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [isRunning, selectedProject]);

  // Reset character filters and load played audio times when project changes
  useEffect(() => {
    setTimeout(() => {
      setActiveCharacterFilters([]);
      if (selectedProject) {
        try {
          const saved = localStorage.getItem(`played_audio_${selectedProject}`);
          setPlayedAudioTimes(saved ? JSON.parse(saved) : {});
        } catch {
          setPlayedAudioTimes({});
        }
      } else {
        setPlayedAudioTimes({});
      }
    }, 0);
  }, [selectedProject]);

  // Auto-connect to log listener if queue is active
  useEffect(() => {
    if (selectedProject && projectDetails && !isRunning) {
      if (projectDetails.global_queue_active) {
        runTool('listen');
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectDetails, isRunning, selectedProject]);

  // Auto-disconnect when queue is finished
  useEffect(() => {
    if (isRunning && activeAction === 'listen' && projectDetails) {
      if (!projectDetails.global_queue_active) {
        if (eventSourceRef.current) {
          eventSourceRef.current.close();
          eventSourceRef.current = null;
        }
        setTimeout(() => {
          setActiveAction(null);
          setIsRunning(false);
          setTerminalLogs(prev => [...prev, `[INFO] File d'attente vide. Écoute de la console terminée.`]);
        }, 0);
      }
    }
  }, [projectDetails, isRunning, activeAction]);

  // Clean event source on project change or unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
        setActiveAction(null);
      }
    };
  }, [selectedProject]);


  const fetchProjects = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/projects`);
      const data = await res.json();
      setProjects(data);
    } catch (e) {
      console.error('Error fetching projects:', e);
    }
  };

  const fetchVoices = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/voicebox/profiles`);
      const data = await res.json();
      const sorted = (data || []).sort((a, b) => 
        (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' })
      );
      setVoices(sorted);
    } catch (e) {
      console.error('Error fetching voices:', e);
    }
  };

  const parseRanges = (expr) => {
    if (!expr) return [];
    const result = [];
    const parts = expr.split(',');
    for (let part of parts) {
      part = part.trim();
      if (!part) continue;
      if (part.includes('-')) {
        const [start, end] = part.split('-').map(Number);
        if (!isNaN(start) && !isNaN(end)) {
          for (let i = start; i <= end; i++) {
            result.push(i);
          }
        }
      } else {
        const num = Number(part);
        if (!isNaN(num)) {
          result.push(num);
        }
      }
    }
    return result;
  };

  const handleToggleCharacterFilter = (charName) => {
    setActiveCharacterFilters(prev => {
      let next;
      if (prev.includes(charName)) {
        next = prev.filter(c => c !== charName);
      } else {
        next = [...prev, charName];
      }
      
      // Sync dropdown
      if (next.length === 1) {
        setSelectedVoiceFilter(next[0]);
      } else if (next.length > 1) {
        setSelectedVoiceFilter('multi');
      } else {
        setSelectedVoiceFilter('');
      }
      
      return next;
    });
  };

  const handleDropdownVoiceFilterChange = (val) => {
    setSelectedVoiceFilter(val);
    if (val === 'multi') {
      // Keep multiple filters as is
    } else if (val) {
      setActiveCharacterFilters([val]);
    } else {
      setActiveCharacterFilters([]);
    }
  };

   const refreshProject = async (name) => {
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(name)}`);
      const data = await res.json();
      // Réponse périmée (le projet a changé pendant la requête) → on la jette,
      // sinon elle écraserait les données du nouveau projet.
      if (selectedProjectRef.current !== name) return;
      setProjectDetails(data);
      if (data.normalization_rules) {
        setNormalizationRules(data.normalization_rules);
      } else {
        setNormalizationRules([]);
      }
      // Ne pas écraser les saisies de seuils non enregistrées (poll toutes les 2 s).
      if (!qcThresholdsDirtyRef.current) {
        setQcThresholds(data.qc_thresholds || {});
      }
      // Seuils par voix utilisée (dérivés de voices_used).
      const vu = data.voices_used || {};
      setVoicesUsedMeta(Object.fromEntries(Object.entries(vu).map(([n, o]) => [n, { voice_id: o.voice_id }])));
      if (!voiceThresholdsDirtyRef.current) {
        setVoiceThresholds(Object.fromEntries(Object.entries(vu).map(([n, o]) => [n, { ...(o.thresholds || {}) }])));
      }
      const systemStyles = [
        { id: 'modern', name: 'Moderne (Eva (dida) – dialogue)', pattern: '^__ACTORS__\\s*(?:\\(([^)]*)\\))?\\s*[-–\\—]\\s*(.*)$', system: true },
        { id: 'classic', name: 'Classique avec Point (JEANNE. dialogue)', pattern: '^__ACTORS__\\s*\\.\\s*(.*)$', system: true },
        { id: 'moliere', name: 'Alternance Simple (Dorine\\ndialogue)', pattern: '^\\s*__ACTORS__\\s*$', system: true }
      ];
      if (data.custom_play_styles) {
        setPlayStyles([
          ...systemStyles,
          ...data.custom_play_styles.map(s => ({ ...s, system: false }))
        ]);
      } else {
        setPlayStyles(systemStyles);
      }

      if (data.piece_style) {
        if (typeof data.piece_style === 'string') {
          setPieceStyle(data.piece_style);
        } else if (data.piece_style && data.piece_style.id) {
          setPieceStyle(data.piece_style.id);
        }
      }
      if (data.hasOwnProperty('remove_page_numbers')) {
        setRemovePageNumbers(data.remove_page_numbers);
      }
      
      // Initialize voice mapping
      const mapping = { ...(data.voice_mapping || {}) };
      if (data.type === 'theatre') {
        const charactersList = data.custom_characters || data.characters || [];
        charactersList.forEach(actor => {
          if (!mapping[actor]) {
            mapping[actor] = actor; // default mapping to original actor name/id
          }
        });
      } else {
        if (mapping["Narrator"]) {
          setMainNarratorVoice(mapping["Narrator"]);
        } else {
          setMainNarratorVoice("Narrator");
        }
      }
      setVoiceMapping(mapping);
    } catch (e) {
      console.error('Error refreshing project:', e);
    }
  };

  const loadProject = async (name) => {
    selectedProjectRef.current = name;
    setSelectedProject(name);
    setProjectDetails(null);
    setTerminalLogs([]);
    setIsRunning(false);
    qcThresholdsDirtyRef.current = false;  // nouveau projet → charger les seuils du serveur
    voiceThresholdsDirtyRef.current = false;
    setIsLoadingProject(true);
    try {
      await refreshProject(name);
    } finally {
      setIsLoadingProject(false);
    }
  };

  const handleCreateProject = async (e) => {
    e.preventDefault();
    if (!newProjectName.trim()) return;
    try {
      const res = await fetch(`${API_BASE}/api/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          name: newProjectName.trim(),
          type: newProjectType
        })
      });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de la création du projet');
        return;
      }
      setNewProjectName('');
      setNewProjectType('novel');
      setIsCreateOpen(false);
      fetchProjects();
    } catch (e) {
      console.error(e);
    }
  };

  const handleStopQueue = async () => {
    if (!selectedProject) return;
    const confirmStop = window.confirm(
      isFr 
        ? "Voulez-vous vraiment stopper le job en cours et vider la file d'attente ?" 
        : "Are you sure you want to stop the active job and clear the queue?"
    );
    if (!confirmStop) return;

    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/queue/stop`, {
        method: 'POST'
      });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de l\'arrêt de la file d\'attente');
      } else {
        await refreshProject(selectedProject);
      }
    } catch (e) {
      console.error("Error stopping queue:", e);
      alert(isFr ? "Erreur réseau lors de l'arrêt de la file d'attente" : "Network error stopping queue");
    }
  };

  // Run subprocess script via Server-Sent Events (SSE)
  function runTool(action, queryParams = {}) {
    if (isRunning) {
      if (action === 'generate') {
        const postUrl = `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/queue`;
        fetch(postUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ranges: queryParams.ranges || 'all',
            voice: queryParams.voice || '',
            versions: queryParams.versions || 1,
            qc_threshold: queryParams.qc_threshold ?? null,
            max_attempts: queryParams.max_attempts ?? 20,
            qc_batch: queryParams.qc_batch ?? false,
          })
        })
        .then(async (res) => {
          if (res.ok) {
            await res.json();
            const rangesDesc = queryParams.ranges ? `segments ${queryParams.ranges}` : 'tous les segments';
            setTerminalLogs(prev => [...prev, `[INFO] Ajouté à la file d'attente : ${rangesDesc} (voix: ${queryParams.voice || 'défaut'})`]);
            refreshProject(selectedProject);
          } else {
            const err = await res.json();
            alert(err.detail || (isFr ? "Erreur lors de l'ajout à la file d'attente" : "Error enqueuing segments"));
          }
        })
        .catch(err => {
          console.error("Queue request failed:", err);
          alert(isFr ? "Erreur réseau lors de l'ajout à la file d'attente" : "Network error enqueuing segments");
        });
        return;
      }
      
      if (activeAction === 'listen' || action === 'concat') {
        // Interrompre l'écoute passive (ou laisser passer concat qui est toujours autorisé)
        if (eventSourceRef.current) {
          eventSourceRef.current.close();
          eventSourceRef.current = null;
        }
        setActiveAction(null);
        setIsRunning(false);
      } else {
        // Un vrai outil tourne déjà, bloquer les autres requêtes d'outils
        return;
      }
    }

    setIsRunning(true);
    setTerminalLogs(action === 'listen' ? [`[INFO] Reconnexion à la console...`] : [`[INFO] Commande lancée...`]);
    setActiveAction(action);

    const params = new URLSearchParams({ action, ...queryParams });
    const sseUrl = `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/terminal?${params.toString()}`;
    const eventSource = new EventSource(sseUrl);
    eventSourceRef.current = eventSource;

    eventSource.onmessage = (event) => {
      let line = event.data;

      // SSE keeps connection open but backend appends newlines
      setTerminalLogs(prev => [...prev, line]);

      // Boucle QC : capter le n° de tentative « [QC] ... tentative 12/20 ... »
      // → affiché « En cours (12) » et relance l'animation de progression.
      const mAtt = line.match(/[Tt]entative\s+(\d+)\s*\//);
      if (mAtt) {
        setQcAttempt(parseInt(mAtt[1], 10));
        if (parseInt(mAtt[1], 10) === 1) setQcBestPct(null); // nouvelle série
        if (activeSegmentStartRef.current) {
          activeSegmentStartRef.current.startTime = Date.now();
        }
        setActiveSegmentProgress(0);
      }
      // Meilleur score retenu : « ... (meilleur : 22%) ... »
      const mBest = line.match(/meilleur\s*:\s*(\d+|—|-)/i);
      if (mBest) {
        setQcBestPct(/\d/.test(mBest[1]) ? parseInt(mBest[1], 10) : null);
      }

      // Détecter si un fichier audio vient d'être généré avec succès pour rafraîchir la liste à chaud
      // On accepte soit le nom .wav, soit un message de réussite avec le numéro de segment
      const isWavGenerated = (line.includes('.wav') || line.toLowerCase().includes('généré') || line.toLowerCase().includes('genere')) && (
        line.toLowerCase().includes('succ') || 
        line.toLowerCase().includes('généré') || 
        line.toLowerCase().includes('genere') || 
        line.toLowerCase().includes('gnr')
      );
      
      if (isWavGenerated) {
        refreshProject(selectedProject);
      }
      
      if (line.includes('[INFO] Commande terminée avec le code de sortie')) {
        eventSource.close();
        eventSourceRef.current = null;
        setActiveAction(null);
        setIsRunning(false);
        setQcAttempt(null);
        // Refresh project details to show updated segments/WAVs without clearing logs
        refreshProject(selectedProject);
      }
    };

    eventSource.onerror = (err) => {
      console.error('SSE Error:', err);
      setTerminalLogs(prev => [...prev, `[ERREUR] Connexion SSE interrompue.`]);
      eventSource.close();
      eventSourceRef.current = null;
      setActiveAction(null);
      setIsRunning(false);
      setQcAttempt(null);
      setQcBestPct(null);
      // Refresh project details to show updated segments/WAVs without clearing logs
      refreshProject(selectedProject);
    };
  };

  const handleUploadFile = async (file) => {
    if (!file) return;
    const isEpub = file.name.toLowerCase().endsWith('.epub');
    const isTxt = file.name.toLowerCase().endsWith('.txt');
    const isPdf = file.name.toLowerCase().endsWith('.pdf');
    // Roman : EPUB ou PDF (le PDF est souvent plus simple à linéariser que l'EPUB).
    const isAllowed = projectDetails?.type === 'theatre' ? (isPdf || isTxt) : (isEpub || isPdf);

    if (!isAllowed) {
      alert(isFr
        ? (projectDetails?.type === 'theatre' ? 'Seuls les fichiers .pdf et .txt sont acceptés' : 'Seuls les fichiers .epub et .pdf sont acceptés')
        : (projectDetails?.type === 'theatre' ? 'Only .pdf and .txt files are accepted' : 'Only .epub and .pdf files are accepted')
      );
      return;
    }

    setUploading(true);
    setTerminalLogs(prev => [...prev, `[INFO] Téléversement du fichier : ${file.name}...`]);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/upload`, {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        const fileExt = file.name.split('.').pop().toUpperCase();
        setTerminalLogs(prev => [...prev, `[OK] Fichier ${fileExt} téléversé avec succès.`]);
        await refreshProject(selectedProject);
      } else {
        const err = await res.json();
        alert(err.detail || 'Erreur lors du téléversement');
      }
    } catch (e) {
      console.error(e);
      alert('Erreur réseau lors du téléversement');
    } finally {
      setUploading(false);
    }
  };

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleUploadFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      handleUploadFile(e.target.files[0]);
    }
  };

  const handleUpdateSegment = async () => {
    if (!editingSegment) return;
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${editingSegment.num}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: editText,
          profile_id: editProfileId
        })
      });
      if (res.ok) {
        setEditingSegment(null);
        // refreshProject met à jour la table en place (sans la vider ni
        // afficher le spinner), contrairement à loadProject.
        refreshProject(selectedProject);
      } else {
        alert('Erreur lors de la mise à jour du segment');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleSplitSegment = async () => {
    if (!editingSegment) return;
    let p1, p2;
    try {
      p1 = JSON.parse(splitPart1);
    } catch (e) {
      alert(isFr ? "Erreur de syntaxe JSON dans la Zone 1 : " + e.message : "JSON syntax error in Zone 1: " + e.message);
      return;
    }
    try {
      p2 = JSON.parse(splitPart2);
    } catch (e) {
      alert(isFr ? "Erreur de syntaxe JSON dans la Zone 2 : " + e.message : "JSON syntax error in Zone 2: " + e.message);
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${editingSegment.num}/split`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ part1: p1, part2: p2 })
      });
      if (res.ok) {
        setEditingSegment(null);
        setIsSplitting(false);
        loadProject(selectedProject);
      } else {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de la scission du segment');
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? 'Erreur réseau lors de la scission' : 'Network error during split');
    }
  };

  const handleDeleteSegment = async () => {
    if (!editingSegment) return;
    const confirmMsg = isFr 
      ? `Êtes-vous sûr de vouloir supprimer le Segment n° ${editingSegment.num} ?\nTous les segments suivants seront automatiquement décalés de -1.`
      : `Are you sure you want to delete Segment #${editingSegment.num}?\nAll subsequent segments will be automatically shifted down by -1.`;
    
    if (!window.confirm(confirmMsg)) return;

    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${editingSegment.num}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        setEditingSegment(null);
        setIsSplitting(false);
        loadProject(selectedProject);
      } else {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de la suppression du segment');
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? 'Erreur réseau lors de la suppression' : 'Network error during deletion');
    }
  };

  const getQcScoreStyle = (score) => {
    // Quality Control similarity score color thresholds:
    // Score >= 80% (0.80) -> Green
    // Score >= 70% (0.70) -> Light Green
    // Score >= 60% (0.60) -> Yellow
    // Score >= 45% (0.45) -> Orange
    // Score < 45%  (0.45) -> Red
    let color = '#f87171'; // Red text
    let border = 'rgba(239, 68, 68, 0.4)';
    let bg = 'rgba(239, 68, 68, 0.1)';

    if (score >= 0.80) {
      color = '#34d399'; // Green text
      border = 'rgba(16, 185, 129, 0.4)';
      bg = 'rgba(16, 185, 129, 0.1)';
    } else if (score >= 0.70) {
      color = '#a7f3d0'; // Light green text
      border = 'rgba(52, 211, 153, 0.4)';
      bg = 'rgba(52, 211, 153, 0.1)';
    } else if (score >= 0.60) {
      color = '#facc15'; // Bright yellow text
      border = 'rgba(234, 179, 8, 0.4)';
      bg = 'rgba(234, 179, 8, 0.1)';
    } else if (score >= 0.45) {
      color = '#fb923c'; // Orange text
      border = 'rgba(249, 115, 22, 0.4)';
      bg = 'rgba(249, 115, 22, 0.1)';
    }

    return {
      marginLeft: '6px',
      fontSize: '11px',
      fontWeight: 'bold',
      minWidth: '34px',
      padding: '0 4px',
      borderColor: border,
      background: bg,
      color: color
    };
  };

  const handleGetQcScore = async (segNum) => {
    setQcScoresLoading(prev => ({ ...prev, [segNum]: true }));
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${segNum}/qc_score`, {
        method: 'POST'
      });
      if (res.ok) {
        const data = await res.json();
        setQcScores(prev => ({ ...prev, [segNum]: data.score }));
      } else {
        const err = await res.json();
        setErrorDetailMsg(err.detail || 'Erreur lors du calcul du score QC.');
      }
    } catch (e) {
      console.error(e);
      setErrorDetailMsg(isFr ? 'Erreur réseau lors du calcul du score.' : 'Network error during score calculation.');
    } finally {
      setQcScoresLoading(prev => ({ ...prev, [segNum]: false }));
    }
  };

  const handleRecomputeQc = () => {
    if (!selectedProject) return;
    const actor = (selectedVoiceFilter && selectedVoiceFilter !== 'multi') ? selectedVoiceFilter : '';
    setQcRecomputeLoading(true);
    setQcScores({});  // vide le cache local → relit les scores du projet une fois terminé
    setTerminalLogs(prev => [...prev, `[INFO] Recalcul QC natif (${actor || 'tous les acteurs'})...`]);

    // Flux SSE avec progression (X/Y) : indispensable sur un gros projet
    // (plusieurs milliers de segments) où l'ancien appel bloquant unique ne
    // donnait aucun retour pendant plusieurs minutes → semblait « ne rien faire ».
    const url = `${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/qc/recompute${actor ? `?actor=${encodeURIComponent(actor)}` : ''}`;
    const es = new EventSource(url);
    es.onmessage = (event) => {
      const line = event.data;
      setTerminalLogs(prev => [...prev, line]);
      if (line.includes('[INFO] Commande terminée avec le code de sortie')) {
        es.close();
        setQcRecomputeLoading(false);
        refreshProject(selectedProject);
      }
    };
    es.onerror = () => {
      es.close();
      setQcRecomputeLoading(false);
      setErrorDetailMsg(isFr ? 'Erreur réseau lors du recalcul QC.' : 'Network error during QC recompute.');
      refreshProject(selectedProject);
    };
  };

  // Tranches de durée pour les seuils QC (borne haute, en secondes).
  const QC_BUCKETS = [
    { key: '0.5', label: '≤0.5s' },
    { key: '1', label: '≤1s' },
    { key: '2', label: '≤2s' },
    { key: '3', label: '≤3s' },
    { key: '4', label: '≤4s' },
    { key: '5', label: '≤5s' },
    { key: '6', label: '≤6s' },
    { key: '7', label: '≤7s' },
    { key: '10', label: '≤10s' },
    { key: '15', label: '>10s' },
  ];

  const qcCharacters = () => {
    if (!projectDetails) return [];
    if (projectDetails.type === 'theatre') {
      return (projectDetails.custom_characters && projectDetails.custom_characters.length
        ? projectDetails.custom_characters
        : projectDetails.characters) || [];
    }
    return ['Narrator'];
  };

  // Seuils applicables à un segment : PAR VOIX réelle (generated_voice) en
  // priorité — c'est l'étalon contre lequel le .wav est noté — sinon repli sur
  // le rôle (théâtre).
  const thresholdMapForSeg = (seg) =>
    voiceThresholds[seg.generated_voice] || qcThresholds[seg.profile_id] || null;

  const getQcAnomalies = () => {
    if (!projectDetails || !projectDetails.segments) return [];

    return projectDetails.segments.filter(seg => {
      const actor = seg.profile_id;
      // Filtre par rôle actif (cohérent avec la table centrale et la barre batch).
      if (activeCharacterFilters.length > 0 && !activeCharacterFilters.includes(actor)) return false;
      const hasAnyAudio = seg.has_audio || (seg.available_versions && seg.available_versions.length > 0);
      if (!hasAnyAudio) return false;

      // Refus manuel : force l'affichage dans QC MÊME si la note est bonne
      // (inverse de 'forced'). La note reste intacte, on outrepasse la visibilité.
      if (seg.qc_status === 'force_refused') return true;

      // Statut QC manuel : forcé (override) ou accepté → toujours ignoré,
      // même si la note n'est pas atteinte.
      if (seg.qc_status === 'forced' || seg.qc_status === 'accepted') return false;

      const thrMap = thresholdMapForSeg(seg);
      if (!thrMap) return false;

      // Même arrondi que le badge affiché (Math.round), sinon un score type
      // 29.6 % affiché « 30 % » resterait « sous le seuil » et la ligne ne
      // pourrait jamais être validée/retirée.
      const score = (seg.qc_score !== undefined && seg.qc_score !== null) ? Math.round(seg.qc_score * 100) : null;

      const duration = seg.duration || 0;
      let bucketKey = '15';
      if (duration <= 0.5) bucketKey = '0.5';
      else if (duration <= 1) bucketKey = '1';
      else if (duration <= 2) bucketKey = '2';
      else if (duration <= 3) bucketKey = '3';
      else if (duration <= 4) bucketKey = '4';
      else if (duration <= 5) bucketKey = '5';
      else if (duration <= 6) bucketKey = '6';
      else if (duration <= 7) bucketKey = '7';
      else if (duration <= 10) bucketKey = '10';

      const threshold = thrMap[bucketKey];
      if (threshold === undefined || threshold === null || threshold === '') return false;

      const isBelow = score === null || score < Number(threshold);
      // Sous le seuil → affiché (à traiter : régénérer, écouter, ou forcer).
      // Au-dessus du seuil → masqué (auto-fiable, on fait confiance à la note).
      // Le clic droit → Forcer permet de garder un segment sous-seuil jugé bon.
      return isBelow;
    });
  };

  // Vrai si le tableau QC mêle plusieurs voix (generated_voice) → on affiche
  // alors une colonne « Voix » pour savoir contre quelle étalon chaque .wav est noté.
  const qcVoicesMixed = (() => {
    if (!projectDetails || !projectDetails.segments) return false;
    const voices = new Set(
      projectDetails.segments.map(s => s.generated_voice).filter(Boolean)
    );
    return voices.size > 1;
  })();

  // Largeur de l'étiquette de nom = nom le plus long du projet (plafonné à
  // 15 caractères ; au-delà → troncature + infobulle). Cartes cohérentes.
  // Un roman à personnages (rôles distribués) se comporte comme du théâtre pour
  // le tableau central et la résolution des voix (locuteur → voix).
  const isTheatreLike = projectDetails?.type === 'theatre' || projectDetails?.type === 'novel_multi';

  // Exclusion mutuelle TTS <-> analyse IA (elles se disputent le GPU).
  // État dérivé des jobs asynchrones remontés par le polling.
  const iaBusy = Object.values(aiJobs).some(j => j && (j.status === 'queued' || j.status === 'running'));
  const ttsBusy = isRunning && activeAction !== 'listen';        // génération TTS en cours
  // Chunks actuellement sous analyse IA (en file ou en cours) — pour ne griser
  // NARRATEUR que sur ceux-là.
  const chunkUnderIa = (idx) => {
    const j = aiJobs[idx];
    return !!j && (j.status === 'queued' || j.status === 'running');
  };

  const nameColWidth = (() => {
    const chars = qcCharacters();
    const maxLen = chars.reduce((m, c) => Math.max(m, (c || '').length), 4);
    // Plafond resserré (11 car.) + peu de padding : évite le grand jour entre
    // le nom et les seuils, et le débordement à droite du panneau.
    return `calc(${Math.min(10, maxLen)}ch + 8px)`;
  })();

  const setQcThreshold = (char, bucket, value) => {
    qcThresholdsDirtyRef.current = true;
    setQcThresholds(prev => {
      const next = { ...prev, [char]: { ...(prev[char] || {}) } };
      if (value === '' || value === null || value === undefined) {
        delete next[char][bucket];
      } else {
        next[char][bucket] = Number(value);
      }
      return next;
    });
  };

  const handleSaveQcThresholds = async () => {
    if (!selectedProject) return;
    setQcThresholdsSaving(true);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/qc_thresholds`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qc_thresholds: qcThresholds }),
      });
      if (res.ok) {
        qcThresholdsDirtyRef.current = false;  // saisies enregistrées → poll peut resynchroniser
        setTerminalLogs(prev => [...prev, '[OK] Seuils QC enregistrés.']);
      } else {
        const err = await res.json();
        setErrorDetailMsg(err.detail || "Erreur lors de l'enregistrement des seuils.");
      }
    } catch (e) {
      console.error(e);
      setErrorDetailMsg(isFr ? 'Erreur réseau.' : 'Network error.');
    } finally {
      setQcThresholdsSaving(false);
    }
  };

  const setVoiceThreshold = (voiceName, bucket, value) => {
    voiceThresholdsDirtyRef.current = true;
    setVoiceThresholds(prev => {
      const next = { ...prev, [voiceName]: { ...(prev[voiceName] || {}) } };
      if (value === '' || value === null || value === undefined) {
        delete next[voiceName][bucket];
      } else {
        next[voiceName][bucket] = Number(value);
      }
      return next;
    });
  };

  const handleSaveVoiceThresholds = async () => {
    setQcThresholdsSaving(true);
    try {
      // Traduire voice_name → voice_id via voicesUsedMeta.
      const payload = {};
      Object.entries(voiceThresholds).forEach(([vname, buckets]) => {
        const vid = voicesUsedMeta[vname]?.voice_id;
        if (vid) payload[vid] = buckets;
      });
      const res = await fetch(`${API_BASE}/api/qc/voice_thresholds`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_thresholds: payload }),
      });
      if (res.ok) {
        voiceThresholdsDirtyRef.current = false;
        setTerminalLogs(prev => [...prev, '[OK] Seuils par voix enregistrés.']);
        if (selectedProject) refreshProject(selectedProject);
      } else {
        const err = await res.json();
        setErrorDetailMsg(err.detail || "Erreur lors de l'enregistrement des seuils par voix.");
      }
    } catch (e) {
      console.error(e);
      setErrorDetailMsg(isFr ? 'Erreur réseau.' : 'Network error.');
    } finally {
      setQcThresholdsSaving(false);
    }
  };

  const handleAddCustomCharacterSubmit = async (e) => {
    e.preventDefault();
    if (!newCharacterName.trim()) return;
    const nameToAdd = newCharacterName.trim().toUpperCase();
    
    // Check if character already exists in custom_characters
    const existing = projectDetails?.custom_characters || [];
    if (existing.includes(nameToAdd)) {
      alert(isFr ? "Ce personnage existe déjà." : "This character already exists.");
      return;
    }
    
    const updatedList = [...existing, nameToAdd];
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/custom_characters`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ custom_characters: updatedList })
      });
      if (res.ok) {
        setNewCharacterName('');
        // Refresh project details to update custom_characters list
        refreshProject(selectedProject);
      } else {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de l\'ajout du personnage');
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? 'Erreur réseau lors de l\'ajout' : 'Network error during character add');
    }
  };

  const handleDeleteCustomCharacter = async (actorToDelete) => {
    const confirmMsg = isFr 
      ? `Êtes-vous sûr de vouloir retirer le personnage "${actorToDelete}" du projet ?`
      : `Are you sure you want to remove the character "${actorToDelete}" from the project?`;
      
    if (!window.confirm(confirmMsg)) return;

    const existing = projectDetails?.custom_characters || [];
    const updatedList = existing.filter(c => c !== actorToDelete);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/custom_characters`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ custom_characters: updatedList })
      });
      if (res.ok) {
        // Clear filter if it was active
        if (activeCharacterFilters.includes(actorToDelete)) {
          setActiveCharacterFilters(prev => prev.filter(c => c !== actorToDelete));
        }
        refreshProject(selectedProject);
      } else {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de la suppression du personnage');
      }
    } catch (e) {
      console.error(e);
      alert(isFr ? 'Erreur réseau lors de la suppression' : 'Network error during character deletion');
    }
  };

  const handleVoiceMapChange = async (actor, voiceName) => {
    // 1. Update local mapping UI state
    const newMapping = { ...voiceMapping, [actor]: voiceName };
    setVoiceMapping(newMapping);

    // 2. Call backend to save mapping in meta.json
    setTerminalLogs(prev => [...prev, `[INFO] Enregistrement de l'assignation : ${actor} → ${voiceName}`]);
    
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/voice_mapping`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_mapping: newMapping })
      });
      if (res.ok) {
        setTerminalLogs(prev => [...prev, `[OK] Configuration de la voix pour ${actor} enregistrée.`]);
        refreshProject(selectedProject);
      } else {
        alert("Erreur lors de la sauvegarde du mapping des voix");
      }
    } catch (e) {
      console.error("Error saving voice mapping:", e);
    }
  };

  // Marque un segment comme écouté (read=1) côté serveur (persistant dans le
  // JSON, partagé entre navigateurs) + mise à jour optimiste locale.
  const markSegmentRead = (num) => {
    setProjectDetails(prev => {
      if (!prev || !prev.segments) return prev;
      return { ...prev, segments: prev.segments.map(s => s.num === num ? { ...s, read: 1 } : s) };
    });
    if (!selectedProject) return;
    fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${num}/mark_read?value=1`, {
      method: 'POST'
    }).catch(e => console.error('mark_read failed:', e));
  };

  // Statut QC manuel persistant : 'forced' (clic droit = override), 'accepted'
  // (validé), 'clear' (retire). Mise à jour optimiste + POST.
  const setSegmentQcStatus = (num, status) => {
    const resolved = status === 'clear' ? null : status;
    setProjectDetails(prev => {
      if (!prev || !prev.segments) return prev;
      return { ...prev, segments: prev.segments.map(s => s.num === num ? { ...s, qc_status: resolved } : s) };
    });
    if (!selectedProject) return;
    fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${num}/qc_status?status=${status}`, {
      method: 'POST'
    }).catch(e => console.error('qc_status failed:', e));
  };

  const playSegmentAudio = (seg, source = 'central') => {
    setLastPlaySource(source);
    if (seg.available_versions && seg.available_versions.length > 0) {
      setVersionSelectionSegment(seg);
      setSelectedVersion(seg.available_versions[0]);
      markSegmentRead(seg.num);
      return;
    }

    markSegmentRead(seg.num);

    // Mark segment as played
    const now = Date.now();
    const updated = { ...playedAudioTimes, [seg.num]: now };
    setPlayedAudioTimes(updated);
    if (selectedProject) {
      try {
        localStorage.setItem(`played_audio_${selectedProject}`, JSON.stringify(updated));
      } catch (e) {
        console.error("Failed to save played audio times:", e);
      }
    }

    const wavUrl = `${API_BASE}${seg.audio_url}?t=${new Date().getTime()}`;
    const voiceName = seg.generated_voice || (
      isTheatreLike
        ? (voiceMapping[seg.profile_id] || seg.profile_id)
        : seg.profile_id
    );

    setActiveAudio({
      url: wavUrl,
      title: `Segment n° ${seg.num}`,
      subtitle: selectedProject,
      voice: voiceName ? `Voix : ${voiceName}` : '',
      num: seg.num
    });
  };

  const handlePlayPreview = (v, isFromAutoplay = false) => {
    if (!versionSelectionSegment) return;
    
    if (!isFromAutoplay) {
      setVersionAutoplayActive(false);
    }
    
    const padNum = String(versionSelectionSegment.num).padStart(5, '0');
    const url = `${API_BASE}/audio/${encodeURIComponent(selectedProject)}/audio/seg${padNum}_${v}.wav?t=${new Date().getTime()}`;
    
    if (playingVersionRef.current === v) {
      if (previewAudioRef.current) {
        if (previewAudioRef.current.paused) {
          previewAudioRef.current.play().catch(err => console.error("Play error:", err));
        } else {
          previewAudioRef.current.pause();
          setVersionAutoplayActive(false);
        }
      }
    } else {
      setPlayingVersion(v);
      if (previewAudioRef.current) {
        previewAudioRef.current.src = url;
        previewAudioRef.current.load();
        previewAudioRef.current.play().catch(err => console.error("Play error:", err));
      }
    }
  };

  const toggleVersionAutoplay = () => {
    if (versionAutoplayActiveRef.current) {
      setVersionAutoplayActive(false);
      if (previewAudioRef.current) {
        previewAudioRef.current.pause();
      }
      setPlayingVersion(null);
    } else {
      setVersionAutoplayActive(true);
      const startVersion = selectedVersion || versionSelectionSegment.available_versions[0];
      setSelectedVersion(startVersion);
      handlePlayPreview(startVersion, true);
    }
  };

  const handleSelectVersion = async () => {
    if (!versionSelectionSegment || !selectedVersion) return;
    
    if (previewAudioRef.current) {
      previewAudioRef.current.pause();
    }
    setVersionAutoplayActive(false);
    setPlayingVersion(null);
    setPreviewIsPlaying(false);
 
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${versionSelectionSegment.num}/select_version`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: selectedVersion })
      });
      
      if (res.ok) {
        setVersionSelectionSegment(null);
        setSelectedVersion('');
        await refreshProject(selectedProject);
      } else {
        const err = await res.json();
        alert(err.detail || 'Erreur lors de la sélection de la version');
      }
    } catch (e) {
      console.error(e);
      alert('Erreur réseau lors de la sélection de la version');
    }
  };

  const handleCancelSelection = () => {
    if (previewAudioRef.current) {
      previewAudioRef.current.pause();
    }
    setVersionAutoplayActive(false);
    setPlayingVersion(null);
    setPreviewIsPlaying(false);
    setVersionSelectionSegment(null);
    setSelectedVersion('');
  };

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = playerVolume;
    }
  }, [activeAudio, playerVolume]);

  useEffect(() => {
    if (!scrollTrackingActive || !activeAudio || !activeAudio.num) return;
    
    const timer = setTimeout(() => {
      const container = segmentListContainerRef.current;
      if (!container) return;
      
      const activeRow = container.querySelector(`#seg-row-${activeAudio.num}`);
      if (!activeRow) return;
      
      const rows = container.querySelectorAll('.segment-row');
      const index = Array.from(rows).indexOf(activeRow);
      
      if (index >= 2) {
        const targetRow = rows[index - 2];
        const containerTop = container.getBoundingClientRect().top;
        const targetRowTop = targetRow.getBoundingClientRect().top;
        const header = container.querySelector('thead');
        const headerHeight = header ? header.getBoundingClientRect().height : 0;
        
        const scrollTarget = container.scrollTop + (targetRowTop - containerTop - headerHeight);
        container.scrollTo({
          top: scrollTarget,
          behavior: 'smooth'
        });
      } else {
        container.scrollTo({
          top: 0,
          behavior: 'smooth'
        });
      }
    }, 100);
    
    return () => clearTimeout(timer);
  }, [activeAudio, scrollTrackingActive, projectDetails?.segments, activeCharacterFilters]);

  // Récupère la vitesse TTS mesurée par le backend (calibrage dynamique).
  useEffect(() => {
    let cancelled = false;
    const fetchSpeed = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/tts_speed`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) { setTtsSpeed(data); ttsSpeedRef.current = data; }
      } catch { /* ignore */ }
    };
    fetchSpeed();
    const id = setInterval(fetchSpeed, 20000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Scores QC de chaque version, chargés à l'ouverture de la modal de sélection.
  useEffect(() => {
    if (!versionSelectionSegment || !selectedProject) { setVersionScores({}); return; }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/segments/${versionSelectionSegment.num}/versions_qc`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setVersionScores(data.scores || {});
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [versionSelectionSegment, selectedProject]);

  useEffect(() => {
    const activeNum = projectDetails?.active_segment;
    if (!activeNum) {
      setTimeout(() => {
        setActiveSegmentProgress(0);
      }, 0);
      activeSegmentStartRef.current = null;
      return;
    }

    if (activeSegmentStartRef.current && activeSegmentStartRef.current.num === activeNum) {
      return;
    }

    const activeSeg = projectDetails?.segments?.find(s => s.num === activeNum);
    if (!activeSeg) return;

    const charCount = activeSeg.text ? activeSeg.text.length : 0;
    const versions = segmentVersions[activeNum] || 1;
    // Ratio dynamique mesuré par le backend (ms/caractère + latence de base),
    // calé sur le matériel/backend réel. Repli GPU-raisonnable si non encore connu.
    const spd = ttsSpeedRef.current;
    const rate = (spd && spd.ms_per_char) ? spd.ms_per_char : 80;   // ms/caractère
    const base = (spd && spd.base_ms != null) ? spd.base_ms : 500;  // ms de base
    const estimatedDuration = (base + charCount * rate) * versions;

    activeSegmentStartRef.current = {
      num: activeNum,
      startTime: Date.now(),
      duration: estimatedDuration
    };
    setTimeout(() => {
      setActiveSegmentProgress(0);
    }, 0);
  }, [projectDetails?.active_segment, projectDetails?.segments, segmentVersions]);

  useEffect(() => {
    const interval = setInterval(() => {
      if (!activeSegmentStartRef.current) {
        setActiveSegmentProgress(0);
        return;
      }
      const { startTime, duration } = activeSegmentStartRef.current;
      const elapsed = Date.now() - startTime;
      const progress = Math.min(95, (elapsed / duration) * 100);
      setActiveSegmentProgress(progress);
    }, 100);

    return () => clearInterval(interval);
  }, []);

  const handleVolumeChange = (e) => {
    if (e.target) {
      const vol = e.target.volume;
      setPlayerVolume(vol);
      localStorage.setItem('playerVolume', vol.toString());
    }
  };

  const getAutoplaySegments = () => {
    const isQcAutoplay = lastPlaySource === 'qc' && !qcAutoplayUseCentral;
    if (isQcAutoplay) {
      return getQcAnomalies();
    }
    return projectDetails?.segments?.filter(
      seg => activeCharacterFilters.length === 0 || activeCharacterFilters.includes(seg.profile_id)
    ) || [];
  };

  const handleAudioEnded = () => {
    if (!autoplayEnabled || !activeAudio || !activeAudio.num) return;
    const currentNum = activeAudio.num;
    const list = getAutoplaySegments();
    const nextSeg = list.find(s => s.num > currentNum && s.has_audio);
    if (nextSeg) {
      playSegmentAudio(nextSeg, lastPlaySource);
    }
  };

  const handlePlayNextSegment = () => {
    if (!activeAudio || !activeAudio.num) return;
    const currentNum = activeAudio.num;
    const list = getAutoplaySegments();
    const nextSeg = list.find(s => s.num > currentNum && s.has_audio);
    if (nextSeg) {
      playSegmentAudio(nextSeg, lastPlaySource);
    }
  };

  const handlePlayPrevSegment = () => {
    if (!activeAudio || !activeAudio.num) return;
    const currentNum = activeAudio.num;
    const list = getAutoplaySegments();
    const prevSeg = [...list].reverse().find(s => s.num < currentNum && s.has_audio);
    if (prevSeg) {
      playSegmentAudio(prevSeg, lastPlaySource);
    }
  };

  const handleRegenerateActiveSegment = () => {
    if (!activeAudio || !activeAudio.num) return;
    const seg = projectDetails?.segments?.find(s => s.num === activeAudio.num);
    if (!seg) return;
    setQcScores(prev => {
      const next = { ...prev };
      delete next[seg.num];
      return next;
    });
    runTool('generate', {
      ranges: seg.num.toString(),
      voice: projectDetails.type === 'theatre' ? seg.profile_id : (projectDetails.type === 'novel_multi' ? '' : mainNarratorVoice),
      versions: segmentVersions[seg.num] || 1
    });
  };

  const showStatusColumn = !!(projectDetails?.global_queue_active || isRunning);
  const activeSegment = projectDetails?.active_segment;
  const pendingSegments = projectDetails?.pending_segments || [];

  return (
    <div className="app-container">
      {/* HEADER */}
      <header style={{ position: 'relative' }}>
        {selectedProject && (
          <div style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            fontSize: '20px',
            fontWeight: 600,
            color: 'var(--text-main)',
            textShadow: '0 0 10px rgba(139, 92, 246, 0.25)',
            whiteSpace: 'nowrap',
            maxWidth: '45%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            pointerEvents: 'none'
          }} title={selectedProject}>
            {selectedProject}
          </div>
        )}
        <div 
          className="brand" 
          onClick={() => { if (selectedProject) { selectedProjectRef.current = null; setSelectedProject(null); } }}
          style={{ 
            cursor: selectedProject ? 'pointer' : 'default',
            transition: 'opacity 0.2s'
          }}
          onMouseEnter={(e) => {
            if (selectedProject) e.currentTarget.style.opacity = '0.85';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.opacity = '1';
          }}
          title={selectedProject ? (isFr ? "Retourner au tableau de bord" : "Back to dashboard") : ""}
        >
          <BookOpen size={28} color="#8b5cf6" />
          <div>
            <h1>AudioBookStudio</h1>
            <span>Orchestrator</span>
          </div>
        </div>
        
        {!selectedProject && (
          <button className="btn btn-primary" onClick={() => setIsCreateOpen(true)}>
            <FolderPlus size={18} />
            Nouveau Projet
          </button>
        )}
      </header>

      {/* DASHBOARD PAGE (LIST PROJECTS) */}
      {!selectedProject ? (
        <div className="main-content">
          <h2 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '16px' }}>Vos Livres & Audiolivres</h2>
          
          {projects.length === 0 ? (
            <div className="empty-state glass-panel">
              <FolderOpen size={48} />
              <p>Aucun projet d'audiolivre n'a été détecté dans votre dossier `Projects/`.</p>
              <button className="btn btn-primary" onClick={() => setIsCreateOpen(true)}>
                Créer un premier projet
              </button>
            </div>
          ) : (
            <div className="dashboard-grid">
              {projects.map(proj => {
                const percent = proj.total_segments > 0 
                  ? Math.round((proj.generated_audio / proj.total_segments) * 100) 
                  : 0;

                return (
                  <div key={proj.name} className="glass-panel project-card" onClick={() => loadProject(proj.name)}>
                    <h3>{proj.name}</h3>
                    <div className="project-meta">
                      {proj.type === 'theatre' && (
                        <span className="badge badge-theatre">
                          Théâtre (multi-personnages)
                        </span>
                      )}
                      {proj.type === 'novel' && (
                        <span className="badge badge-novel">
                          Roman (mono narrateur)
                        </span>
                      )}
                      {proj.type === 'novel_multi' && (
                        <span className="badge badge-novel-multi">
                          Roman (avec personnages)
                        </span>
                      )}
                      {proj.has_final_mp3 && (
                        <span className="badge badge-completed">
                          <CheckCircle2 size={12} />
                          MP3 Exporté
                        </span>
                      )}
                    </div>
                    
                    <div className="project-progress">
                      <div className="progress-header">
                        <span>Audio généré</span>
                        <span>{proj.generated_audio} / {proj.total_segments} ({percent}%)</span>
                      </div>
                      <div className="progress-track">
                        <div className="progress-bar" style={{ width: `${percent}%` }}></div>
                      </div>
                    </div>
                    
                    <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'flex-end', color: '#8b5cf6', fontSize: '14px', fontWeight: 500 }}>
                      Ouvrir le projet <ChevronRight size={16} style={{ marginLeft: '4px', alignSelf: 'center' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        /* PROJECT DETAILS PAGE */
        <div className={`project-detail-layout ${isQcOpen ? (isQcExpanded ? 'qc-mode qc-expanded' : 'qc-mode') : ''}`}>
          {/* LEFT COLUMN: VOICE CONFIGURATION */}
          <div className="side-column">
            {projectDetails && (
              <div 
                className="glass-panel control-card"
                style={isQcExpanded ? { maxWidth: '100%', width: '100%', height: '100%', minHeight: 0, display: 'flex', flexDirection: 'row', gap: '24px', alignItems: 'stretch', overflow: 'hidden' } : {}}
              >
                <div style={isQcExpanded ? { width: '500px', minWidth: '500px', display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, overflowY: 'auto' } : { display: 'contents' }}>
                  {projectDetails.type === 'novel_multi' && !isQcExpanded && (
                    <button
                      className="btn btn-secondary"
                      style={{
                        width: '100%', marginBottom: '12px',
                        borderColor: 'rgba(139, 92, 246, 0.6)',
                        background: 'rgba(139, 92, 246, 0.12)',
                        color: '#a78bfa', fontWeight: 600,
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px'
                      }}
                      onClick={handleToggleAiRoles}
                      title={isFr ? "Découper le roman et attribuer les rôles avec l'IA" : "Chunk the novel and attribute roles with AI"}
                    >
                      🧠 {isAiRolesOpen ? (isFr ? 'Fermer IA & Rôles' : 'Close AI & Roles') : (isFr ? 'IA & Rôles' : 'AI & Roles')}
                    </button>
                  )}
                  <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', width: '100%' }}>
                    <button
                      className="btn btn-secondary"
                      style={{
                        flex: 1,
                        borderColor: isQcOpen ? 'rgba(52, 211, 153, 0.6)' : 'rgba(52, 211, 153, 0.4)',
                        background: isQcOpen ? 'rgba(52, 211, 153, 0.12)' : 'rgba(52, 211, 153, 0.05)',
                        color: '#34d399',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px'
                      }}
                      onClick={() => {
                         const opening = !isQcOpen;
                         setIsQcOpen(opening);
                         if (isQcOpen) {
                           setIsQcExpanded(false);
                         }
                         setManuallyValidated({});
                         
                         if (opening && projectDetails && projectDetails.segments) {
                           const newAudios = {};
                           projectDetails.segments.forEach(seg => {
                             const lastPlayed = playedAudioTimes[seg.num];
                             const isNew = (seg.has_audio || (seg.available_versions && seg.available_versions.length > 0)) && (
                               !lastPlayed || (seg.audio_mtime && (seg.audio_mtime * 1000) > lastPlayed)
                             );
                             if (isNew) {
                               newAudios[seg.num] = true;
                             }
                           });
                           setQcSessionNewAudios(newAudios);
                         } else {
                           setQcSessionNewAudios({});
                         }
                       }}
                      title={isFr ? "Quality Center : seuils QC par personnage et durée" : "Quality Center"}
                    >
                      <Target size={13} />
                      {isQcOpen ? (isFr ? 'Fermer Quality Center' : 'Close Quality Center') : 'Quality Center'}
                    </button>
                    {isQcOpen && (
                      <button
                        className="btn btn-secondary"
                        style={{
                          padding: '0 12px',
                          borderColor: 'rgba(167, 139, 250, 0.4)',
                          background: isQcExpanded ? 'rgba(167, 139, 250, 0.15)' : 'rgba(167, 139, 250, 0.05)',
                          color: '#a78bfa',
                          display: 'flex', alignItems: 'center', justifyContent: 'center'
                        }}
                        onClick={() => setIsQcExpanded(v => !v)}
                        title={isFr ? (isQcExpanded ? "Réduire le volet" : "Agrandir le volet") : (isQcExpanded ? "Collapse panel" : "Expand panel")}
                      >
                        {isQcExpanded ? "◀" : "▶"}
                      </button>
                    )}
                  </div>
                  <h4>Configuration des Voix</h4>

                {projectDetails.type === 'novel' && (
                  <div className="panel-section">
                    <span className="label-title">Narrateur Principal</span>
                    <div className="voice-mapping-voice-line" style={{ paddingLeft: 0 }}>
                      <select 
                        value={mainNarratorVoice} 
                        onChange={(e) => {
                          const val = e.target.value;
                          setMainNarratorVoice(val);
                          handleVoiceMapChange("Narrator", val);
                        }}
                      >
                        {voices.map(v => (
                          <option key={v.id} value={v.name}>{v.name} ({v.language})</option>
                        ))}
                      </select>
                    </div>
                  </div>
                )}

                {/* Seuils QC par VOIX utilisée (romans mono/multi-voix) */}
                {isQcOpen && (projectDetails.type === 'novel' || projectDetails.type === 'novel_multi') && (
                  <div className="panel-section">
                    <span className="label-title">{isFr ? 'Voix utilisées (seuils QC)' : 'Voices used (QC thresholds)'}</span>
                    {Object.keys(voiceThresholds).length === 0 ? (
                      <p style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', marginTop: '6px' }}>
                        {isFr ? "Aucune voix générée pour l'instant (génère des segments)." : "No generated voice yet."}
                      </p>
                    ) : (
                      <>
                        <div style={{ display: 'grid', gridTemplateColumns: `140px repeat(10, 30px) auto`, columnGap: '3px', alignItems: 'center', marginTop: '8px', marginBottom: '4px' }}>
                          <div className="vmc-name-spacer">{isFr ? 'Voix' : 'Voice'}</div>
                          {QC_BUCKETS.map(b => <span key={b.key} className="vmc-th-head">{b.label}</span>)}
                        </div>
                        {Object.keys(voiceThresholds).map(vname => (
                          <div key={vname} style={{ display: 'grid', gridTemplateColumns: `140px repeat(10, 30px) auto`, columnGap: '3px', alignItems: 'center', marginBottom: '6px' }}>
                            <span title={vname} style={{ fontSize: '11px', fontWeight: 600, color: 'var(--accent)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{vname}</span>
                            {QC_BUCKETS.map(b => (
                              <input
                                key={b.key}
                                className="vmc-th-input"
                                type="number" min="0" max="99" placeholder="—" title={`${vname} — ${b.label}`}
                                value={voiceThresholds[vname]?.[b.key] ?? ''}
                                onChange={e => setVoiceThreshold(vname, b.key, e.target.value)}
                              />
                            ))}
                          </div>
                        ))}
                        <button
                          className="btn btn-primary"
                          style={{ width: '100%', marginTop: '8px' }}
                          onClick={handleSaveVoiceThresholds}
                          disabled={qcThresholdsSaving}
                        >
                          {qcThresholdsSaving ? (isFr ? 'Enregistrement…' : 'Saving…') : (isFr ? 'Enregistrer les seuils (voix)' : 'Save thresholds (voice)')}
                        </button>
                      </>
                    )}
                  </div>
                )}

                {(projectDetails.type === 'theatre' || projectDetails.type === 'novel_multi') && (
                  <div className="panel-section">
                    <span className="label-title">Distribution (Acteur → Voix)</span>

                    {/* Character Add Form */}
                    <form 
                      onSubmit={handleAddCustomCharacterSubmit}
                      style={{ 
                        display: 'flex', 
                        gap: '8px', 
                        marginBottom: '16px',
                        background: 'rgba(255, 255, 255, 0.03)',
                        padding: '8px',
                        borderRadius: '6px',
                        border: '1px solid rgba(255, 255, 255, 0.05)'
                      }}
                    >
                      <input
                        type="text"
                        value={newCharacterName}
                        onChange={(e) => setNewCharacterName(e.target.value.toUpperCase())}
                        placeholder={isFr ? "AJOUTER UN ACTEUR..." : "ADD AN ACTOR..."}
                        style={{
                          flex: 1,
                          background: 'rgba(0, 0, 0, 0.2)',
                          border: '1px solid rgba(255, 255, 255, 0.1)',
                          borderRadius: '4px',
                          color: '#fff',
                          padding: '4px 8px',
                          fontSize: '12px',
                          textTransform: 'uppercase'
                        }}
                      />
                      <button
                        type="submit"
                        className="btn-icon"
                        style={{
                          minWidth: '28px',
                          width: '28px',
                          height: '28px',
                          minHeight: '28px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          background: 'var(--accent)',
                          color: '#fff',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: 'pointer'
                        }}
                        title={isFr ? "Ajouter le personnage" : "Add character"}
                      >
                        <Plus size={14} />
                      </button>
                    </form>

                    {isQcOpen && (
                      <div
                        className="vmc-header"
                        style={{ display: 'grid', gridTemplateColumns: `${nameColWidth} repeat(10, 30px) auto`, columnGap: '3px', alignItems: 'center' }}
                      >
                        <div className="vmc-name-spacer">{isFr ? 'Seuil' : 'QC'}</div>
                        {QC_BUCKETS.map(b => <span key={b.key} className="vmc-th-head">{b.label}</span>)}
                      </div>
                    )}
                    <div className="voice-mapping-list">
                      {(projectDetails.custom_characters || []).map(actor => {
                        const isDetected = (projectDetails.characters || []).includes(actor);
                        // Roman : les personnages secondaires sont toujours assignables
                        // (pas encore "détectés" dans les segments tant qu'on ne les a
                        // pas affectés à des chunks via l'éditeur).
                        const isNovel = projectDetails.type !== 'theatre';
                        return (
                          <div key={actor} className="voice-mapping-card" style={{ opacity: (isNovel || isDetected) ? 1 : 0.65 }}>
                            <div
                              className="voice-mapping-actor-line"
                              style={isQcOpen
                                ? { display: 'grid', gridTemplateColumns: `${nameColWidth} repeat(10, 30px) auto`, columnGap: '3px', alignItems: 'center' }
                                : { display: 'flex', alignItems: 'center', gap: '8px' }}
                            >
                              <span
                                className={`character-name character-name-toggle ${activeCharacterFilters.includes(actor) ? 'active' : ''}`}
                                title={
                                  !isDetected
                                    ? (isFr ? `${actor} (Non détecté dans les segments)` : `${actor} (Not detected in segments)`)
                                    : actor
                                }
                                onClick={() => {
                                  if (isDetected) {
                                    handleToggleCharacterFilter(actor);
                                  }
                                }}
                                style={isQcOpen ? {
                                  cursor: isDetected ? 'pointer' : 'default',
                                  width: nameColWidth,
                                  maxWidth: nameColWidth,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                } : {
                                  // Hors QC : le nom prend la place dispo et passe à la
                                  // ligne si besoin (plus de troncature "GRAND...").
                                  cursor: isDetected ? 'pointer' : 'default',
                                  flex: '1 1 auto',
                                  minWidth: 0,
                                  whiteSpace: 'normal',
                                  overflowWrap: 'anywhere',
                                }}
                              >
                                {actor}
                              </span>

                              {isQcOpen && QC_BUCKETS.map(b => (
                                <input
                                  key={b.key}
                                  className="vmc-th-input"
                                  type="number" min="0" max="99" placeholder="—" title={b.label}
                                  value={qcThresholds[actor]?.[b.key] ?? ''}
                                  onChange={e => setQcThreshold(actor, b.key, e.target.value)}
                                />
                              ))}

                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: 'auto' }}>
                                {(projectDetails.ai_seen_characters || []).includes(actor) && (
                                  <span
                                    title={isFr ? "Vu dans l'analyse des rôles (pas encore dans les TTS)" : "Seen in role analysis (not yet in TTS)"}
                                    style={{ display: 'inline-flex', alignItems: 'center', color: '#34d399' }}
                                  >
                                    <Eye size={13} />
                                  </span>
                                )}
                                {!isDetected && (
                                  <span style={{ fontSize: '10px', color: '#f87171', fontStyle: 'italic' }}>
                                    {isFr ? "Non détecté" : "Not detected"}
                                  </span>
                                )}
                                {!isDetected && actor !== 'DIDAS' && (
                                  <button
                                    className="btn-icon"
                                    style={{
                                      color: '#ef4444',
                                      minWidth: '24px',
                                      width: '24px',
                                      height: '24px',
                                      minHeight: '24px',
                                      padding: 0,
                                      display: 'flex',
                                      alignItems: 'center',
                                      justifyContent: 'center',
                                      background: 'transparent',
                                      border: 'none',
                                      cursor: 'pointer'
                                    }}
                                    title={isFr ? "Supprimer ce personnage" : "Delete this character"}
                                    onClick={() => handleDeleteCustomCharacter(actor)}
                                  >
                                    <Trash2 size={12} />
                                  </button>
                                )}
                              </div>
                            </div>
                            <div className="voice-mapping-voice-line">
                              <select
                                value={voiceMapping[actor] || actor}
                                onChange={(e) => handleVoiceMapChange(actor, e.target.value)}
                                disabled={!isNovel && !isDetected}
                                style={{ cursor: (isNovel || isDetected) ? 'default' : 'not-allowed' }}
                              >
                                <option value="DIDAS">Didascalies (Narrateur)</option>
                                {voices.map(v => (
                                  <option key={v.id} value={v.name}>{v.name}</option>
                                ))}
                              </select>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {isQcOpen && (
                      <button
                        className="btn btn-primary"
                        style={{ width: '100%', marginTop: '10px' }}
                        onClick={handleSaveQcThresholds}
                        disabled={qcThresholdsSaving}
                      >
                        {qcThresholdsSaving ? (isFr ? 'Enregistrement…' : 'Saving…') : (isFr ? 'Enregistrer les seuils QC' : 'Save QC thresholds')}
                      </button>
                    )}
                  </div>
                )}
                </div>
                
                {isQcExpanded && (
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, minWidth: '400px', borderLeft: '1px solid rgba(255,255,255,0.08)', paddingLeft: '24px', overflow: 'hidden' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                      <h4 style={{ margin: 0 }}>
                        {isFr ? "⚠️ Segments hors-seuil QC" : "⚠️ Segments below QC thresholds"}
                        <span className="badge" style={{ marginLeft: '8px', background: 'rgba(239, 68, 68, 0.2)', color: '#f87171' }}>
                          {getQcAnomalies().length}
                        </span>
                      </h4>
                    </div>

                    {/* Barre de génération batch QC (plage + personnage + versions) :
                        chaque segment de la sélection lance sa propre boucle QC
                        (seuil résolu par segment côté serveur). */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '14px', padding: '10px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
                      <span style={{ fontSize: '13px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{isFr ? 'Plage :' : 'Range:'}</span>
                      <input
                        type="text"
                        placeholder="Ex: 1-10, 15, 20-30"
                        className="input-range"
                        style={{ flexGrow: 1, minWidth: '120px', maxWidth: '200px' }}
                        value={generationRange}
                        onChange={(e) => setGenerationRange(e.target.value)}
                      />
                      {isTheatreLike && (
                        <select
                          style={{ minWidth: '150px', maxWidth: '200px' }}
                          value={selectedVoiceFilter}
                          onChange={(e) => handleDropdownVoiceFilterChange(e.target.value)}
                        >
                          <option value="">{isFr ? 'Tous les acteurs' : 'All actors'}</option>
                          {activeCharacterFilters.length > 1 && (
                            <option value="multi" disabled>
                              {isFr ? `Filtres multiples (${activeCharacterFilters.length})` : `Multiple filters (${activeCharacterFilters.length})`}
                            </option>
                          )}
                          {projectDetails.characters.map(char => (
                            <option key={char} value={char}>{char}</option>
                          ))}
                        </select>
                      )}
                      <select
                        className="version-select"
                        value={qcBatchVersions}
                        onChange={(e) => setQcBatchVersions(parseInt(e.target.value))}
                        title={isFr ? "Nombre de versions par segment" : "Versions per segment"}
                      >
                        <option value={1}>1x</option>
                        <option value={3}>3x</option>
                        <option value={5}>5x</option>
                        <option value={10}>10x</option>
                      </select>
                      <button
                        className="btn btn-primary"
                        style={{ padding: '8px 14px', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}
                        disabled={isRunning}
                        onClick={() => {
                          // On ne cible QUE les segments réellement affichés dans le
                          // tableau QC (sous le seuil + filtre rôle actif), jamais
                          // « tous ». La plage ne fait que restreindre cet ensemble.
                          let targetNums = getQcAnomalies().map(seg => seg.num);
                          if (generationRange) {
                            const wantedNums = parseRanges(generationRange);
                            targetNums = targetNums.filter(n => wantedNums.includes(n));
                          }
                          if (targetNums.length === 0) {
                            alert(isFr
                              ? "Aucun segment sous le seuil à régénérer (vérifie le filtre de rôle et la plage)."
                              : "No below-threshold segment to regenerate (check role filter and range).");
                            return;
                          }
                          runTool('generate', {
                            ranges: targetNums.join(','),
                            voice: projectDetails.type === 'theatre'
                              ? (activeCharacterFilters.length === 1 ? activeCharacterFilters[0] : '')
                              : mainNarratorVoice,
                            versions: qcBatchVersions,
                            qc_batch: true,
                          });
                        }}
                      >
                        <Cpu size={14} />
                        {isFr ? 'Générer la sélection (QC)' : 'Generate selection (QC)'}
                      </button>
                    </div>

                    <div className="segment-list-container" style={{ flex: 1, overflowY: 'auto', background: 'rgba(0,0,0,0.2)' }}>
                      <table className="segment-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', position: 'sticky', top: 0, background: '#0a0814', zIndex: 1 }}>
                            <th style={{ padding: '8px', width: '50px', fontSize: '12px', color: 'var(--text-muted)' }}>N°</th>
                            <th style={{ padding: '8px', width: '100px', fontSize: '12px', color: 'var(--text-muted)' }}>Acteur</th>
                            {qcVoicesMixed && <th style={{ padding: '8px', width: '120px', fontSize: '12px', color: 'var(--text-muted)' }}>Voix</th>}
                            <th style={{ padding: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>Texte</th>
                            <th style={{ padding: '8px', width: '110px', fontSize: '12px', color: 'var(--text-muted)' }}>Statut</th>
                            <th style={{ padding: '8px', width: '110px', fontSize: '12px', color: 'var(--text-muted)' }}>Score (Durée)</th>
                            <th style={{ padding: '8px', width: '180px', fontSize: '12px', color: 'var(--text-muted)', textAlign: 'center' }}>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {getQcAnomalies().map(seg => {
                            const isPlaying = activeAudio && activeAudio.url.includes(`/audio/${seg.filename.replace('.json', '.wav').replace('.txt', '.wav')}`);
                            const queueIndex = pendingSegments.findIndex(item => item.num === seg.num);
                            const isQueued = queueIndex !== -1;
                            const isActive = activeSegment === seg.num;
                            const scorePct = seg.qc_score ? Math.round(seg.qc_score * 100) : null;
                            const durationText = seg.duration ? `${seg.duration.toFixed(1)}s` : '—';
                            
                            const isNewAudio = (seg.has_audio || (seg.available_versions && seg.available_versions.length > 0)) && seg.read === 0;

                            let bucketKey = '15';
                            if (seg.duration <= 0.5) bucketKey = '0.5';
                            else if (seg.duration <= 1) bucketKey = '1';
                            else if (seg.duration <= 2) bucketKey = '2';
                            else if (seg.duration <= 3) bucketKey = '3';
                            else if (seg.duration <= 4) bucketKey = '4';
                            else if (seg.duration <= 5) bucketKey = '5';
                            else if (seg.duration <= 6) bucketKey = '6';
                            else if (seg.duration <= 7) bucketKey = '7';
                            else if (seg.duration <= 10) bucketKey = '10';
                            const targetVal = (voiceThresholds[seg.generated_voice] || qcThresholds[seg.profile_id])?.[bucketKey] || '—';

                            return (
                              <tr
                                key={seg.num}
                                style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', background: isActive ? 'rgba(139, 92, 246, 0.08)' : 'transparent' }}
                                onContextMenu={(e) => {
                                  // Layer invisible : le clic droit fait APPARAÎTRE une
                                  // carte « Forcer ». Il faut cliquer dedans pour confirmer
                                  // (un simple clic droit serait trop dangereux).
                                  e.preventDefault();
                                  setQcContextMenu({ num: seg.num, x: e.clientX, y: e.clientY, mode: 'force' });
                                }}
                              >
                                <td style={{ padding: '8px', fontSize: '12px', textAlign: 'center', color: 'var(--text-muted)' }}>
                                  #{seg.num}
                                </td>
                                <td style={{ padding: '8px', fontSize: '12px' }}>
                                  <span
                                    className={`role-badge ${activeCharacterFilters.includes(seg.profile_id) ? 'active' : ''}`}
                                    data-speaker={seg.profile_id}
                                    style={{ fontStyle: seg.profile_id === 'DIDAS' ? 'italic' : 'normal' }}
                                    onClick={() => handleToggleCharacterFilter(seg.profile_id)}
                                    title={isFr ? `Filtrer par ${seg.profile_id}` : `Filter by ${seg.profile_id}`}
                                  >
                                    {seg.profile_id}
                                  </span>
                                </td>
                                {qcVoicesMixed && (
                                  <td style={{ padding: '8px', fontSize: '11px', color: 'var(--text-muted)' }} title={isFr ? "Voix étalon (VoiceBox) contre laquelle ce segment est noté" : "Reference voice this segment is scored against"}>
                                    {seg.generated_voice || '—'}
                                  </td>
                                )}
                                <td
                                  onClick={() => {
                                    setEditFromQc(true);
                                    setEditingSegment(seg);
                                    setEditText(seg.text);
                                    setEditProfileId(seg.profile_id || "Narrator");
                                  }}
                                  title={isFr ? "Éditer / supprimer ce segment" : "Edit / delete this segment"}
                                  style={{
                                    padding: '8px', fontSize: '12px', whiteSpace: 'normal', wordBreak: 'break-word', color: 'var(--text-main)', cursor: 'pointer',
                                    ...(isActive ? { background: `linear-gradient(90deg, rgba(251, 191, 36, 0.18) 0%, rgba(251, 191, 36, 0.18) ${activeSegmentProgress}%, rgba(251, 191, 36, 0.04) ${activeSegmentProgress}%, rgba(251, 191, 36, 0.04) 100%)` } : {})
                                  }}>
                                  {seg.text}
                                </td>
                                <td style={{ padding: '8px', fontSize: '12px', verticalAlign: 'middle' }}>
                                  {isActive ? (
                                    <span className="status-badge processing-badge" style={{ fontSize: '11px', color: '#fbbf24', fontWeight: 500 }}>
                                      {isFr ? 'En cours' : 'Processing'}{qcAttempt != null ? ` (${qcAttempt}${qcBestPct != null ? ` - ${qcBestPct}%` : ''})` : '…'}
                                    </span>
                                  ) : isQueued ? (
                                    <span className="status-badge queued-badge" style={{ fontSize: '11px', color: '#a78bfa', fontWeight: 500 }}
                                      title={isFr ? `${pendingSegments[queueIndex].global_position}e dans la file d'attente` : `${pendingSegments[queueIndex].global_position} in queue`}>
                                      {isFr ? 'Attente' : 'Queued'} ({pendingSegments[queueIndex].global_position})
                                    </span>
                                  ) : null}
                                </td>
                                <td style={{ padding: '8px', fontSize: '12px', verticalAlign: 'middle' }}>
                                   <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '8px' }}>
                                     <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                       <span style={{
                                         color: targetVal !== '—' && scorePct < Number(targetVal) ? '#f87171' : '#34d399',
                                         fontWeight: 'bold',
                                         fontSize: '11px',
                                         display: 'inline-flex',
                                         alignItems: 'center'
                                       }}>
                                         {scorePct}% <span style={{ fontWeight: 'normal', color: 'var(--text-muted)', marginLeft: '4px', fontSize: '10px' }}>({targetVal}%)</span>
                                         {targetVal !== '—' && scorePct >= Number(targetVal) && (
                                           <span style={{ marginLeft: '4px', color: '#34d399', fontSize: '10px' }}>✓</span>
                                         )}
                                       </span>
                                       <span style={{ color: 'var(--text-muted)', fontSize: '10px' }}>
                                         {durationText}
                                       </span>
                                     </div>

                                     {!isActive && targetVal !== '—' && scorePct >= Number(targetVal) && (
                                       <button
                                         className="btn-icon active"
                                         title={isFr ? "Valider et accepter (QC)" : "Validate and accept (QC)"}
                                         onClick={() => { markSegmentRead(seg.num); setSegmentQcStatus(seg.num, 'accepted'); }}
                                         style={{
                                           width: '32px',
                                           height: '32px',
                                           borderRadius: '50%',
                                           background: 'rgba(255, 255, 255, 0.05)',
                                           borderColor: 'rgba(255, 255, 255, 0.1)',
                                           color: 'rgba(255, 255, 255, 0.3)',
                                           flexShrink: 0,
                                           display: 'inline-flex',
                                           alignItems: 'center',
                                           justifyContent: 'center',
                                           transition: 'all 0.2s ease',
                                           cursor: 'pointer'
                                         }}
                                       >
                                         <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                                           <polyline points="20 6 9 17 4 12"></polyline>
                                         </svg>
                                       </button>
                                     )}
                                   </div>
                                 </td>
                                <td style={{ padding: '8px', verticalAlign: 'middle' }}>
                                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
                                    <button
                                      className="btn-icon active"
                                      title={isFr ? "Localiser dans la table centrale" : "Locate in central table"}
                                      onClick={() => {
                                        const rowEl = document.getElementById(`seg-row-${seg.num}`);
                                        if (rowEl) {
                                          rowEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                          rowEl.style.backgroundColor = 'rgba(251, 191, 36, 0.3)';
                                          setTimeout(() => {
                                            rowEl.style.backgroundColor = '';
                                          }, 2000);
                                        }
                                      }}
                                      style={{
                                        width: '32px',
                                        height: '32px',
                                        borderRadius: '50%',
                                        background: 'rgba(139, 92, 246, 0.25)',
                                        borderColor: '#8b5cf6',
                                        color: '#c084fc',
                                        flexShrink: 0,
                                        display: 'inline-flex',
                                        alignItems: 'center',
                                        justifyContent: 'center'
                                      }}
                                    >
                                      <Target size={16} />
                                    </button>

                                    {(seg.has_audio || (seg.available_versions && seg.available_versions.length > 0)) ? (
                                      <button 
                                        className={`btn-icon active ${seg.available_versions && seg.available_versions.length > 0 ? 'has-versions' : ''} ${isNewAudio ? 'new-audio' : ''}`} 
                                        onClick={() => playSegmentAudio(seg, 'qc')}
                                        title={seg.available_versions && seg.available_versions.length > 0 ? (isFr ? "Sélectionner une version alternative" : "Select an alternative version") : (isFr ? "Lire l'audio" : "Play audio")}
                                      >
                                        {seg.available_versions && seg.available_versions.length > 0 ? (
                                          <Music size={12} />
                                        ) : (
                                          <Play size={12} fill="currentColor" />
                                        )}
                                      </button>
                                    ) : (
                                      !isActive && !isQueued && <span style={{ fontSize: '11px', color: 'var(--text-dark)' }}>Sans Audio</span>
                                    )}

                                    <select
                                      className="version-select"
                                      value={segmentVersions[seg.num] || 1}
                                      onChange={(e) => setSegmentVersions(prev => ({ ...prev, [seg.num]: parseInt(e.target.value) }))}
                                      title={isFr ? "Nombre de versions à générer" : "Number of versions to generate"}
                                      disabled={isActive || isQueued}
                                    >
                                      <option value={1}>1x</option>
                                      <option value={3}>3x</option>
                                      <option value={5}>5x</option>
                                      <option value={10}>10x</option>
                                    </select>

                                    <button
                                      className="btn-icon"
                                      title="Régénérer ce segment (boucle QC jusqu'au seuil)"
                                      onClick={() => {
                                        setQcScores(prev => {
                                          const next = { ...prev };
                                          delete next[seg.num];
                                          return next;
                                        });
                                        const threshold = targetVal !== '—' ? Number(targetVal) : undefined;
                                        runTool('generate', {
                                          ranges: seg.num.toString(),
                                          voice: projectDetails.type === 'theatre' ? seg.profile_id : (projectDetails.type === 'novel_multi' ? '' : mainNarratorVoice),
                                          versions: segmentVersions[seg.num] || 1,
                                          ...(threshold !== undefined ? { qc_threshold: threshold } : {}),
                                        });
                                      }}
                                    >
                                      <RefreshCw size={12} className={isActive ? "spin" : ""} />
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* MIDDLE COLUMN: SEGMENTS TABLE */}
          <div className="main-column">
            <div className="glass-panel" style={{ padding: '16px', display: 'flex', justifyContent: 'flex-start', alignItems: 'center' }}>
              {/* Segment range synthesis tool */}
              {projectDetails && projectDetails.segments.length > 0 && (
                <div style={{ 
                  display: 'flex', 
                  justifyContent: 'space-between', 
                  alignItems: 'center', 
                  width: '100%',
                  gap: '12px',
                  flexWrap: 'wrap'
                }}>
                  {/* Left Column: Range inputs */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexGrow: 1, minWidth: '220px' }}>
                    <span style={{ fontSize: '13px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>Plage :</span>
                    <input 
                      type="text" 
                      placeholder="Ex: 1-10, 15, 20-30" 
                      className="input-range"
                      style={{ flexGrow: 1, maxWidth: '240px' }}
                      value={generationRange}
                      onChange={(e) => setGenerationRange(e.target.value)}
                    />
                  </div>
                  
                  {/* Center Column: Actor dropdown */}
                  {isTheatreLike && (
                    <div style={{ display: 'flex', justifyContent: 'center', flexGrow: 1, minWidth: '200px' }}>
                      <select 
                        style={{ width: '100%', maxWidth: '240px' }}
                        value={selectedVoiceFilter}
                        onChange={(e) => handleDropdownVoiceFilterChange(e.target.value)}
                      >
                        <option value="">Tous les acteurs</option>
                        {activeCharacterFilters.length > 1 && (
                          <option value="multi" disabled>
                            {isFr ? `Filtres multiples (${activeCharacterFilters.length})` : `Multiple filters (${activeCharacterFilters.length})`}
                          </option>
                        )}
                        {projectDetails.characters.map(char => (
                          <option key={char} value={char}>{char}</option>
                        ))}
                      </select>
                    </div>
                  )}
                  
                  {/* Right Column: Action buttons */}
                  <div style={{ display: 'flex', justifyContent: 'flex-end', flexGrow: 1, minWidth: '160px', gap: '8px' }}>
                    <button
                      className="btn-icon"
                      style={{ minWidth: '40px', height: '38px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                      title={isFr
                        ? "Recalculer les scores QC (acteur sélectionné, ou tous si 'Tous les acteurs')"
                        : "Recompute QC scores (selected actor, or all)"}
                      onClick={handleRecomputeQc}
                      disabled={qcRecomputeLoading}
                    >
                      {qcRecomputeLoading ? <RefreshCw size={16} className="spin" /> : <Target size={16} />}
                    </button>
                    <button
                      className="btn btn-primary"
                      style={{ padding: '8px 16px', fontSize: '13px', width: '100%', maxWidth: '200px', justifyContent: 'center' }}
                      disabled={iaBusy}
                      title={iaBusy ? (isFr ? "Analyse IA en cours — génération TTS indisponible" : "AI analysis running — TTS generation unavailable") : undefined}
                      onClick={() => {
                        let finalRange = generationRange;
                        let targetNums = [];
                        if (activeCharacterFilters.length > 0) {
                          let targetSegs = projectDetails.segments.filter(seg => activeCharacterFilters.includes(seg.profile_id));
                          if (generationRange) {
                            const wantedNums = parseRanges(generationRange);
                            targetSegs = targetSegs.filter(seg => wantedNums.includes(seg.num));
                          }
                          targetNums = targetSegs.map(seg => seg.num);
                          finalRange = targetNums.join(',');
                        } else if (generationRange) {
                          targetNums = parseRanges(generationRange);
                        }

                        // Clear cached QC scores in React state for these segments
                        if (targetNums.length > 0) {
                          setQcScores(prev => {
                            const next = { ...prev };
                            targetNums.forEach(n => delete next[n]);
                            return next;
                          });
                        } else {
                          // Clear all cached QC scores if regenerating all
                          setQcScores({});
                        }

                        runTool('generate', {
                          ranges: finalRange,
                          voice: isTheatreLike ? (activeCharacterFilters.length === 1 ? activeCharacterFilters[0] : '') : mainNarratorVoice
                        });
                      }}
                    >
                      <Cpu size={14} />
                      Générer la sélection
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* SEGMENT TABLE */}
            <div className={`segment-list-container ${scrollTrackingActive ? 'scroll-tracking-active' : ''}`} ref={segmentListContainerRef}>
              {isLoadingProject ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', padding: '40px' }}>
                  <RefreshCw className="spin" size={48} color="var(--accent-hover)" style={{ marginBottom: '16px', animation: 'spin 1.5s linear infinite' }} />
                  <p style={{ color: 'var(--text-muted)', fontSize: '15px' }}>
                    {isFr ? "Chargement du projet en cours... un instant..." : "Loading project... please wait..."}
                  </p>
                </div>
              ) : projectDetails && projectDetails.segments.length > 0 ? (
                <table className="segment-table">
                  <thead>
                    <tr>
                      <th style={{ width: '60px' }}>Num</th>
                      {isTheatreLike && <th style={{ width: '140px' }}>Rôle</th>}
                      <th>Texte</th>
                      {showStatusColumn && <th style={{ width: '100px', textAlign: 'center' }}>Statut</th>}
                      <th style={{ textAlign: 'right', width: '200px' }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {projectDetails.segments
                      .filter(seg => activeCharacterFilters.length === 0 || activeCharacterFilters.includes(seg.profile_id))
                      .map(seg => {
                      const isPlaying = activeAudio && activeAudio.url.includes(`/audio/${seg.filename.replace('.json', '.wav').replace('.txt', '.wav')}`);
                      const queueIndex = pendingSegments.findIndex(item => item.num === seg.num);
                      const isQueued = queueIndex !== -1;
                      const isActive = activeSegment === seg.num;
                      
                      const isNewAudio = (seg.has_audio || (seg.available_versions && seg.available_versions.length > 0)) && seg.read === 0;

                      let rowClass = "segment-row";
                      if (isPlaying) rowClass += " playing";
                      if (isActive) rowClass += " processing";
                      if (isQueued) rowClass += " queued";

                      return (
                        <tr
                          key={seg.num}
                          className={rowClass}
                          id={`seg-row-${seg.num}`}
                          onContextMenu={(e) => {
                            // Clic droit → carte « Refuser la note » (envoie le
                            // segment dans Quality Center malgré une bonne note).
                            e.preventDefault();
                            setQcContextMenu({ num: seg.num, x: e.clientX, y: e.clientY, mode: 'refuse' });
                          }}
                        >
                          <td className="segment-num">#{seg.num}</td>
                          {isTheatreLike && (
                            <td className="segment-speaker">
                              <span 
                                className={`role-badge ${activeCharacterFilters.includes(seg.profile_id) ? 'active' : ''}`}
                                data-speaker={seg.profile_id}
                                style={{
                                  fontStyle: seg.profile_id === 'DIDAS' ? 'italic' : 'normal',
                                }}
                                onClick={() => handleToggleCharacterFilter(seg.profile_id)}
                                title={isFr ? `Filtrer par ${seg.profile_id}` : `Filter by ${seg.profile_id}`}
                              >
                                {seg.profile_id}
                              </span>
                            </td>
                          )}
                          <td
                            onClick={() => {
                              setEditFromQc(false);
                              setEditingSegment(seg);
                              setEditText(seg.text);
                              setEditProfileId(seg.profile_id || "Narrator");
                            }}
                            className="segment-text-cell"
                            style={isActive ? {
                              cursor: 'pointer',
                              background: `linear-gradient(90deg, rgba(251, 191, 36, 0.18) 0%, rgba(251, 191, 36, 0.18) ${activeSegmentProgress}%, rgba(251, 191, 36, 0.04) ${activeSegmentProgress}%, rgba(251, 191, 36, 0.04) 100%)`
                            } : { cursor: 'pointer' }}
                          >
                            <div className="segment-text editable">
                              {seg.text}
                            </div>
                          </td>
                           {showStatusColumn && (
                            <td style={{ textAlign: 'center', verticalAlign: 'middle', whiteSpace: 'nowrap' }}>
                              {isActive && (
                                <span className="status-badge processing-badge" style={{ fontSize: '11px', color: '#fbbf24', fontWeight: 500 }}>
                                  En cours...
                                </span>
                              )}
                              {isQueued && (
                                <span className="status-badge queued-badge" style={{ fontSize: '11px', color: '#a78bfa', fontWeight: 500 }} title={isFr ? `${pendingSegments[queueIndex].global_position}e dans la file d'attente globale` : `${pendingSegments[queueIndex].global_position} in global queue`}>
                                  Attente ({pendingSegments[queueIndex].global_position})
                                </span>
                              )}
                            </td>
                          )}
                          <td className="segment-actions">
                            {seg.has_audio || (seg.available_versions && seg.available_versions.length > 0) ? (
                              <button 
                                className={`btn-icon active ${seg.available_versions && seg.available_versions.length > 0 ? 'has-versions' : ''} ${isNewAudio ? 'new-audio' : ''}`} 
                                onClick={() => playSegmentAudio(seg)}
                                title={seg.available_versions && seg.available_versions.length > 0 ? (isFr ? "Sélectionner une version alternative" : "Select an alternative version") : (isFr ? "Lire l'audio" : "Play audio")}
                              >
                                {seg.available_versions && seg.available_versions.length > 0 ? (
                                  <Music size={12} />
                                ) : (
                                  <Play size={12} fill="currentColor" />
                                )}
                              </button>
                            ) : (
                              !isActive && !isQueued && <span style={{ fontSize: '11px', color: 'var(--text-dark)' }}>Sans Audio</span>
                            )}
                            
                            <select
                              className="version-select"
                              value={segmentVersions[seg.num] || 1}
                              onChange={(e) => setSegmentVersions(prev => ({ ...prev, [seg.num]: parseInt(e.target.value) }))}
                              title={isFr ? "Nombre de versions à générer" : "Number of versions to generate"}
                              disabled={isActive || isQueued}
                            >
                              <option value={1}>1x</option>
                              <option value={3}>3x</option>
                              <option value={5}>5x</option>
                              <option value={10}>10x</option>
                            </select>

                            <button
                              className="btn-icon"
                              disabled={iaBusy}
                              title={iaBusy ? (isFr ? "Analyse IA en cours — génération TTS indisponible" : "AI analysis running — TTS generation unavailable") : "Régénérer ce segment uniquement"}
                              onClick={() => {
                                // Clear cached QC score in React state for this segment
                                setQcScores(prev => {
                                  const next = { ...prev };
                                  delete next[seg.num];
                                  return next;
                                });
                                runTool('generate', {
                                  ranges: seg.num.toString(),
                                  voice: projectDetails.type === 'theatre' ? seg.profile_id : (projectDetails.type === 'novel_multi' ? '' : mainNarratorVoice),
                                  versions: segmentVersions[seg.num] || 1
                                });
                              }}
                            >
                              <RefreshCw size={12} className={isActive ? "spin" : ""} />
                            </button>

                            {seg.qc_status === 'force_refused' && (
                              <span
                                onClick={() => setSegmentQcStatus(seg.num, 'clear')}
                                title={isFr ? "Refusé → visible dans Quality Center. Cliquer pour annuler le refus." : "Refused → shown in Quality Center. Click to undo."}
                                style={{ marginLeft: '6px', fontSize: '9px', fontWeight: 700, color: '#fff', background: '#ef4444', borderRadius: '4px', padding: '2px 5px', letterSpacing: '0.5px', cursor: 'pointer' }}
                              >
                                REFUSÉ
                              </span>
                            )}

                            {(seg.has_audio || (seg.available_versions && seg.available_versions.length > 0)) && (
                              (() => {
                                const score = qcScores[seg.num] !== undefined ? qcScores[seg.num] : seg.qc_score;
                                const isLoading = qcScoresLoading[seg.num];

                                if (isLoading) {
                                  return (
                                    <button className="btn-icon" style={{ marginLeft: '6px' }} disabled>
                                      <RefreshCw size={12} className="spin" />
                                    </button>
                                  );
                                }

                                // Score numérique : badge coloré, clic = recalcul.
                                if (typeof score === 'number') {
                                  const pct = Math.round(score * 100);
                                  const pass = score >= 0.70;
                                  return (
                                    <button
                                      className="btn-icon"
                                      style={getQcScoreStyle(score)}
                                      onClick={() => handleGetQcScore(seg.num)}
                                      title={isFr ? `Score QC : ${score.toFixed(4)} (${pass ? 'Accepté' : 'Rejeté'}). Re-calculer.` : `QC Score: ${score.toFixed(4)} (${pass ? 'Passed' : 'Failed'}). Recalculate.`}
                                    >
                                      {pct}
                                    </button>
                                  );
                                }

                                // Évalué mais trop court → n/a (clic = re-calcul).
                                if (score === null) {
                                  return (
                                    <button
                                      className="btn-icon"
                                      style={{ marginLeft: '6px', opacity: 0.55, fontSize: '10px' }}
                                      onClick={() => handleGetQcScore(seg.num)}
                                      title={isFr ? "Chunk trop court pour une évaluation fiable (n/a). Re-calculer." : "Chunk too short for reliable evaluation (n/a). Recalculate."}
                                    >
                                      n/a
                                    </button>
                                  );
                                }

                                // Pas encore calculé → bouton de calcul (natif, sans référence manuelle).
                                return (
                                  <button
                                    className="btn-icon"
                                    style={{ marginLeft: '6px' }}
                                    onClick={() => handleGetQcScore(seg.num)}
                                    title={isFr ? "Calculer le score de similarité vocale (QC)" : "Compute voice similarity score (QC)"}
                                  >
                                    <Target size={12} style={{ color: 'var(--accent-hover)' }} />
                                  </button>
                                );
                              })()
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>

              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', padding: '40px' }}>
                  <FileText size={48} color="var(--text-dark)" style={{ marginBottom: '16px' }} />
                  <p style={{ color: 'var(--text-muted)', fontSize: '15px' }}>Aucun segment n'a encore été créé. Lancez l'importation puis le découpage.</p>
                </div>
              )}
            </div>
            
            {/* TERMINAL RESIZER */}
            <div 
              className={`terminal-resizer ${isDraggingTerminal ? 'dragging' : ''}`}
              onMouseDown={handleMouseDown}
            />

            {/* TERMINAL LIVE CONSOLE */}
            <div className="terminal-container" style={{ height: `${terminalHeight}px` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#10b981', borderBottom: '1px solid rgba(16, 185, 129, 0.2)', paddingBottom: '6px', marginBottom: '8px', flexShrink: 0 }}>
                <TerminalIcon size={14} />
                <span style={{ fontWeight: 600 }}>Console d'Exécution en Direct</span>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {isRunning && <RefreshCw size={12} className="spin" style={{ animation: 'spin 1.5s linear infinite' }} />}
                  {projectDetails?.global_queue_active && (
                    <button
                      onClick={handleStopQueue}
                      title={isFr ? "Stopper les jobs TTS en cours et purger la file d'attente" : "Stop active TTS jobs and purge queue"}
                      style={{
                        background: '#ef4444',
                        border: 'none',
                        borderRadius: '2px',
                        width: '12px',
                        height: '12px',
                        cursor: 'pointer',
                        padding: 0,
                        transition: 'background 0.2s',
                      }}
                      onMouseEnter={(e) => e.currentTarget.style.background = '#dc2626'}
                      onMouseLeave={(e) => e.currentTarget.style.background = '#ef4444'}
                    />
                  )}
                </div>
              </div>
              <div style={{ flex: 1, overflowY: 'auto' }}>
                {terminalLogs.length === 0 && <span style={{ color: 'var(--text-dark)' }}>Aucune action lancée dans cette session.</span>}
                {terminalLogs.map((log, idx) => {
                  let className = "terminal-line";
                  if (log.toLowerCase().includes("erreur") || log.toLowerCase().includes("error")) className += " err";
                  if (log.startsWith("[INFO]") || log.startsWith("[OK]")) className += " info";
                  return (
                    <div key={idx} className={className}>
                      {log}
                    </div>
                  );
                })}
                <div ref={terminalEndRef} />
              </div>
            </div>
          </div>
          {/* RIGHT COLUMN: ACTION & SETTINGS PANEL */}
          <div className="side-column">
            {projectDetails && (
              <>
                {/* PIPELINE CONTROL CARD */}
                <div className="glass-panel control-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <h4 style={{ margin: 0 }}>Pipeline de traitement</h4>
                    {projectDetails.type === 'theatre' && (
                      <span className="badge badge-theatre" style={{ fontSize: '11px', padding: '2px 6px' }}>
                        Théâtre (multi-personnages)
                      </span>
                    )}
                    {projectDetails.type === 'novel' && (
                      <span className="badge badge-novel" style={{ fontSize: '11px', padding: '2px 6px' }}>
                        Roman (mono narrateur)
                      </span>
                    )}
                    {projectDetails.type === 'novel_multi' && (
                      <span className="badge badge-novel-multi" style={{ fontSize: '11px', padding: '2px 6px' }}>
                        Roman (avec personnages)
                      </span>
                    )}
                  </div>
                  
                  {projectDetails.type === 'theatre' ? (
                    /* THEATRE PIPELINE (6 STEPS) */
                    <>
                      {/* Step 1: Fichier Source */}
                      <div className="panel-section">
                        <span className="label-title">Étape 1 : Fichier Source (TXT ou PDF)</span>
                        
                        <input 
                          type="file" 
                          ref={fileInputRef} 
                          style={{ display: 'none' }} 
                          accept={projectDetails.type === 'theatre' ? ".txt,.pdf" : ".epub"} 
                          onChange={handleFileChange} 
                        />
                        
                        {projectDetails.pdf_file || projectDetails.formated_txt_file || projectDetails.source_txt_file ? (
                          <div>
                            <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '10px', borderRadius: '6px', border: '1px solid var(--panel-border)', marginBottom: '10px' }}>
                              <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {projectDetails.pdf_file || projectDetails.formated_txt_file || projectDetails.source_txt_file}
                              </div>
                              <span style={{ fontSize: '11px', color: 'var(--success)' }}>
                                {projectDetails.pdf_file ? 'Fichier PDF détecté' :
                                 projectDetails.formated_txt_file ? 'Fichier TXT normalisé détecté' : 'Fichier TXT source détecté'}
                              </span>
                            </div>
                            
                            <div 
                              className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
                              style={{ padding: '12px 10px', fontSize: '12.5px' }}
                              onDragEnter={handleDrag}
                              onDragLeave={handleDrag}
                              onDragOver={handleDrag}
                              onDrop={handleDrop}
                              onClick={() => fileInputRef.current.click()}
                            >
                              <FolderPlus size={16} />
                              <span>
                                {uploading ? 'Téléversement...' : 'Remplacer le fichier source...'}
                              </span>
                            </div>
                          </div>
                        ) : (
                          <div 
                            className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
                            onDragEnter={handleDrag}
                            onDragLeave={handleDrag}
                            onDragOver={handleDrag}
                            onDrop={handleDrop}
                            onClick={() => fileInputRef.current.click()}
                          >
                            <FolderPlus size={30} style={{ marginBottom: '4px' }} />
                            <span style={{ fontSize: '12.5px' }}>
                              {uploading ? (
                                'Téléversement...'
                              ) : (
                                <>Glissez-déposez un fichier <span className="highlight">.txt</span> ou <span className="highlight">.pdf</span> ici ou <span className="highlight">parcourir</span></>
                              )}
                            </span>
                          </div>
                        )}
                        
                        {projectDetails.pdf_file && (
                          <button 
                            className="btn btn-secondary" 
                            style={{ width: '100%', marginTop: '6px' }}
                            onClick={() => runTool('extract', { play_type: 'theatre' })}
                            disabled={(isRunning && activeAction !== 'listen') || uploading}
                          >
                            Convertir le PDF en Texte
                          </button>
                        )}

                        <button 
                          className="btn btn-secondary" 
                          style={{ 
                            width: '100%', 
                            marginTop: '6px', 
                            borderColor: 'rgba(139, 92, 246, 0.4)', 
                            background: 'rgba(139, 92, 246, 0.05)', 
                            color: 'var(--accent-hover)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: '6px'
                          }}
                          onClick={() => setIsNormalizerOpen(true)}
                        >
                          <Sliders size={13} />
                          Atelier de Normalisation
                        </button>
                      </div>

                      {/* Step 2: Parsing */}
                      <div className="panel-section">
                        <span className="label-title">Étape 2 : Parsing de la Pièce</span>
                        {projectDetails.parsed_txt_file ? (
                          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '10px', borderRadius: '6px', border: '1px solid var(--panel-border)', marginBottom: '10px' }}>
                            <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {projectDetails.parsed_txt_file}
                            </div>
                            <span style={{ fontSize: '11px', color: 'var(--success)' }}>Fichier parsé disponible</span>
                          </div>
                        ) : (
                          <div style={{ padding: '8px', border: '1px dashed var(--warning)', borderRadius: '6px', color: 'var(--warning)', fontSize: '12px', marginBottom: '10px' }}>
                            {projectDetails.formated_txt_file ? "Prêt à parser le texte normalisé" :
                             projectDetails.source_txt_file ? "Prêt à parser le texte" : "Importez le fichier texte source d'abord"}
                          </div>
                        )}
                        
                        <button 
                          className="btn btn-secondary" 
                          style={{ width: '100%' }}
                          onClick={() => runTool('parse_theatre')}
                          disabled={(!projectDetails.source_txt_file && !projectDetails.formated_txt_file) || (isRunning && activeAction !== 'listen')}
                        >
                          Parser la Pièce (parse_theatre)
                        </button>
                      </div>

                      {/* Step 3: Chunking */}
                      <div className="panel-section">
                        <span className="label-title">Étape 3 : Découpe en sous-répliques</span>
                        {projectDetails.chunked_txt_file ? (
                          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '10px', borderRadius: '6px', border: '1px solid var(--panel-border)', marginBottom: '10px' }}>
                            <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {projectDetails.chunked_txt_file}
                            </div>
                            <span style={{ fontSize: '11px', color: 'var(--success)' }}>Répliques chunkées disponibles</span>
                          </div>
                        ) : (
                          <div style={{ padding: '8px', border: '1px dashed var(--warning)', borderRadius: '6px', color: 'var(--warning)', fontSize: '12px', marginBottom: '10px' }}>
                            {projectDetails.parsed_txt_file ? "Prêt à appliquer le chunker" : "Veuillez parser la pièce d'abord"}
                          </div>
                        )}
                        
                        <button 
                          className="btn btn-secondary" 
                          style={{ width: '100%' }}
                          onClick={() => runTool('chunk_theatre')}
                          disabled={!projectDetails.parsed_txt_file || (isRunning && activeAction !== 'listen')}
                        >
                          Appliquer le Chunker (chunker_theatre)
                        </button>
                      </div>

                      {/* Step 4: Découpage en segments */}
                      <div className="panel-section">
                        <span className="label-title">Étape 4 : Découpage en Segments TTS</span>
                        {projectDetails.segments.length > 0 ? (
                          <div style={{ padding: '8px', background: 'rgba(16, 185, 129, 0.05)', border: '1px solid rgba(16, 185, 129, 0.2)', borderRadius: '6px', color: 'var(--success)', fontSize: '12px', marginBottom: '10px' }}>
                            {projectDetails.segments.length} segments TTS prêts
                          </div>
                        ) : (
                          <div style={{ padding: '8px', border: '1px dashed var(--warning)', borderRadius: '6px', color: 'var(--warning)', fontSize: '12px', marginBottom: '10px' }}>
                            {projectDetails.chunked_txt_file ? "Prêt à créer les segments" : "Veuillez appliquer le chunker d'abord"}
                          </div>
                        )}
                        
                        <button 
                          className="btn btn-primary" 
                          style={{ 
                            width: '100%', 
                            background: 'rgba(16, 185, 129, 0.1)', 
                            border: '1px solid #10b981', 
                            color: '#34d399', 
                            fontWeight: 700,
                            textShadow: '0 1px 2px rgba(0,0,0,0.5)',
                            boxShadow: '0 4px 12px rgba(16, 185, 129, 0.15)'
                          }}
                          onClick={() => runTool('split')}
                          disabled={!projectDetails.chunked_txt_file || (isRunning && activeAction !== 'listen')}
                        >
                          Créer les Segments TTS (split_theatre)
                        </button>
                      </div>

                      {/* Step 5: Synthèse */}
                      <div className="panel-section">
                        <span className="label-title">Étape 5 : Génération Totale</span>
                        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px' }}>
                          Générer l'audio pour les {projectDetails.segments.length} segments de la pièce.
                        </p>
                        <button 
                          className="btn btn-primary" 
                          style={{ width: '100%' }}
                          onClick={() => runTool('generate', { voice: '' })}
                          disabled={projectDetails.segments.length === 0 || iaBusy}
                          title={iaBusy ? (isFr ? "Analyse IA en cours — génération TTS indisponible" : "AI analysis running — TTS generation unavailable") : undefined}
                        >
                          Générer tout l'Audio
                        </button>
                      </div>

                      {/* Step 6: Compilation MP3 */}
                      <div className="panel-section">
                        <span className="label-title">Étape 6 : Exportation MP3</span>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '10px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '11px', color: 'var(--text-muted)', width: '55px' }}>Plage :</span>
                            <input 
                              type="text" 
                              placeholder="Ex: 1-100 ou vide" 
                              className="input-range"
                              style={{ width: '100%', height: '30px', fontSize: '12px', background: 'rgba(255, 255, 255, 0.03)', border: '1px solid var(--panel-border)', borderRadius: '4px', padding: '0 8px', color: 'var(--text)' }}
                              value={concatRange}
                              onChange={(e) => setConcatRange(e.target.value)}
                            />
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '11px', color: 'var(--text-muted)', width: '55px' }}>Fichier :</span>
                            <input 
                              type="text" 
                              placeholder="Ex: Chapitre 1" 
                              className="input-range"
                              style={{ width: '100%', height: '30px', fontSize: '12px', background: 'rgba(255, 255, 255, 0.03)', border: '1px solid var(--panel-border)', borderRadius: '4px', padding: '0 8px', color: 'var(--text)' }}
                              value={concatFilename}
                              onChange={(e) => setConcatFilename(e.target.value)}
                            />
                          </div>
                        </div>
                        <button 
                          className="btn btn-secondary" 
                          style={{ width: '100%', borderColor: 'rgba(16, 185, 129, 0.4)', background: 'rgba(16, 185, 129, 0.05)' }}
                          onClick={() => runTool('concat', { ranges: concatRange, output_name: concatFilename })}
                          disabled={projectDetails.segments.length === 0}
                        >
                          <Music size={16} />
                          Compiler en Fichier MP3
                        </button>
                      </div>
                    </>
                  ) : (
                    /* NOVEL PIPELINE (4 STEPS) */
                    <>
                      {/* Step 1: Source */}
                      <div className="panel-section">
                        <span className="label-title">Étape 1 : Fichier Source (EPUB ou PDF)</span>

                        <input
                          type="file"
                          ref={fileInputRef}
                          style={{ display: 'none' }}
                          accept=".epub,.pdf"
                          onChange={handleFileChange}
                        />

                        {(projectDetails.epub_file || projectDetails.pdf_file) ? (
                          <div>
                            <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '10px', borderRadius: '6px', border: '1px solid var(--panel-border)', marginBottom: '10px' }}>
                              <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {projectDetails.epub_file || projectDetails.pdf_file}
                              </div>
                              <span style={{ fontSize: '11px', color: 'var(--success)' }}>
                                {projectDetails.pdf_file ? 'Fichier PDF détecté' : 'Fichier EPUB détecté'}
                              </span>
                            </div>

                            <div
                              className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
                              style={{ padding: '12px 10px', fontSize: '12.5px' }}
                              onDragEnter={handleDrag}
                              onDragLeave={handleDrag}
                              onDragOver={handleDrag}
                              onDrop={handleDrop}
                              onClick={() => fileInputRef.current.click()}
                            >
                              <FolderPlus size={16} />
                              <span>
                                {uploading ? 'Téléversement...' : 'Remplacer le fichier source...'}
                              </span>
                            </div>
                          </div>
                        ) : (
                          <div
                            className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
                            onDragEnter={handleDrag}
                            onDragLeave={handleDrag}
                            onDragOver={handleDrag}
                            onDrop={handleDrop}
                            onClick={() => fileInputRef.current.click()}
                          >
                            <FolderPlus size={30} style={{ marginBottom: '4px' }} />
                            <span style={{ fontSize: '12.5px' }}>
                              {uploading ? (
                                'Téléversement...'
                              ) : (
                                <>Glissez-déposez un fichier <span className="highlight">.epub</span> ou <span className="highlight">.pdf</span> ici ou <span className="highlight">parcourir</span></>
                              )}
                            </span>
                          </div>
                        )}

                        <button
                          className="btn btn-secondary"
                          style={{ width: '100%', marginTop: '6px' }}
                          onClick={() => runTool('extract', { play_type: projectDetails.type })}
                          disabled={(!projectDetails.epub_file && !projectDetails.pdf_file) || (isRunning && activeAction !== 'listen') || uploading}
                        >
                          {projectDetails.pdf_file ? 'Convertir le PDF en Texte' : 'Extraire le texte EPUB'}
                        </button>

                        {projectDetails.source_txt_file && (
                          <button
                            className="btn btn-secondary"
                            style={{ width: '100%', marginTop: '6px', borderColor: 'rgba(167, 139, 250, 0.4)', background: 'rgba(167, 139, 250, 0.05)', color: '#a78bfa' }}
                            onClick={() => { setPieceStyle('none'); setIsNormalizerOpen(true); }}
                          >
                            <Sliders size={14} style={{ marginRight: '6px' }} />
                            {isFr ? 'Atelier de Normalisation' : 'Normalization Workshop'}
                          </button>
                        )}
                      </div>

                      {projectDetails.type === 'novel_multi' ? (
                        <>
                          {/* Roman à personnages : parcours théâtre (rôles distribués). */}
                          {/* Étape 2 : le _parsed.txt vient de l'assemblage IA & Rôles. */}
                          <div className="panel-section">
                            <span className="label-title">Étape 2 : Rôles assemblés (IA & Rôles)</span>
                            {projectDetails.parsed_txt_file ? (
                              <div style={{ background: 'rgba(16,185,129,0.05)', padding: '10px', borderRadius: '6px', border: '1px solid rgba(16,185,129,0.2)', marginBottom: '10px' }}>
                                <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {projectDetails.parsed_txt_file}
                                </div>
                                <span style={{ fontSize: '11px', color: 'var(--success)' }}>Livre assemblé (rôles) disponible</span>
                              </div>
                            ) : (
                              <div style={{ padding: '8px', border: '1px dashed var(--warning)', borderRadius: '6px', color: 'var(--warning)', fontSize: '12px', marginBottom: '10px' }}>
                                Ouvrez 🧠 IA & Rôles, validez les chunks, puis « 📚 Assembler le livre ».
                              </div>
                            )}
                          </div>

                          {/* Étape 3 : chunker_theatre */}
                          <div className="panel-section">
                            <span className="label-title">Étape 3 : Découpe en sous-répliques</span>
                            {projectDetails.chunked_txt_file && (
                              <div style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '6px', border: '1px solid var(--panel-border)', marginBottom: '10px' }}>
                                <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{projectDetails.chunked_txt_file}</div>
                                <span style={{ fontSize: '11px', color: 'var(--success)' }}>Répliques chunkées disponibles</span>
                              </div>
                            )}
                            <button
                              className="btn btn-secondary"
                              style={{ width: '100%' }}
                              onClick={() => runTool('chunk_theatre')}
                              disabled={!projectDetails.parsed_txt_file || (isRunning && activeAction !== 'listen')}
                            >
                              Appliquer le Chunker (chunker_theatre)
                            </button>
                          </div>

                          {/* Étape 4 : split_theatre */}
                          <div className="panel-section">
                            <span className="label-title">Étape 4 : Découpage en segments TTS</span>
                            {projectDetails.segments.length > 0 ? (
                              <div style={{ padding: '8px', background: 'rgba(16,185,129,0.05)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: '6px', color: 'var(--success)', fontSize: '12px', marginBottom: '10px' }}>
                                {projectDetails.segments.length} segments TTS prêts
                              </div>
                            ) : (
                              <div style={{ padding: '8px', border: '1px dashed var(--warning)', borderRadius: '6px', color: 'var(--warning)', fontSize: '12px', marginBottom: '10px' }}>
                                {projectDetails.chunked_txt_file ? "Prêt à créer les segments" : "Appliquez le chunker d'abord"}
                              </div>
                            )}
                            <button
                              className="btn btn-primary"
                              style={{ 
                                width: '100%', 
                                background: 'rgba(16, 185, 129, 0.1)', 
                                border: '1px solid #10b981', 
                                color: '#34d399', 
                                fontWeight: 700,
                                textShadow: '0 1px 2px rgba(0,0,0,0.5)',
                                boxShadow: '0 4px 12px rgba(16, 185, 129, 0.15)'
                              }}
                              onClick={() => runTool('split_theatre')}
                              disabled={!projectDetails.chunked_txt_file || (isRunning && activeAction !== 'listen')}
                            >
                              Créer les Segments TTS (split_theatre)
                            </button>
                          </div>
                        </>
                      ) : (
                        /* Roman mono-narrateur : découpage direct (split_book). */
                        <div className="panel-section">
                          <span className="label-title">Étape 2 : Découpage intelligent</span>
                          {projectDetails.source_txt_file ? (
                            <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '10px', borderRadius: '6px', border: '1px solid var(--panel-border)', marginBottom: '10px' }}>
                              <div style={{ fontSize: '13px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {projectDetails.source_txt_file}
                              </div>
                              {projectDetails.segments.length > 0 ? (
                                <span style={{ fontSize: '11px', color: 'var(--success)' }}>
                                  {projectDetails.segments.length} segments configurés
                                </span>
                              ) : (
                                <span style={{ fontSize: '11px', color: 'var(--warning)' }}>
                                  Prêt à être découpé en segments
                                </span>
                              )}
                            </div>
                          ) : (
                            <div style={{ padding: '8px', border: '1px dashed var(--warning)', borderRadius: '6px', color: 'var(--warning)', fontSize: '12px', marginBottom: '10px' }}>
                              Veuillez extraire le texte source d'abord
                            </div>
                          )}

                          <button
                            className="btn btn-secondary"
                            style={{ width: '100%' }}
                            onClick={() => runTool('split')}
                            disabled={!projectDetails.source_txt_file || (isRunning && activeAction !== 'listen')}
                          >
                            Découper en Segments
                          </button>
                        </div>
                      )}

                      {/* Step 3: Synthesis */}
                      <div className="panel-section">
                        <span className="label-title">Étape 3 : Génération Totale</span>
                        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px' }}>
                          Générer la voix pour tous les {projectDetails.segments.length} segments du livre en s'appuyant sur l'API Voicebox en local.
                        </p>
                        <button 
                          className="btn btn-primary" 
                          style={{ width: '100%' }}
                          onClick={() => runTool('generate', {
                            voice: mainNarratorVoice
                          })}
                          disabled={projectDetails.segments.length === 0 || iaBusy}
                          title={iaBusy ? (isFr ? "Analyse IA en cours — génération TTS indisponible" : "AI analysis running — TTS generation unavailable") : undefined}
                        >
                          Générer tout l'Audio
                        </button>
                      </div>

                      {/* Step 4: Export */}
                      <div className="panel-section">
                        <span className="label-title">Étape 4 : Exportation MP3</span>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '10px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '11px', color: 'var(--text-muted)', width: '55px' }}>Plage :</span>
                            <input 
                              type="text" 
                              placeholder="Ex: 1-100 ou vide" 
                              className="input-range"
                              style={{ width: '100%', height: '30px', fontSize: '12px', background: 'rgba(255, 255, 255, 0.03)', border: '1px solid var(--panel-border)', borderRadius: '4px', padding: '0 8px', color: 'var(--text)' }}
                              value={concatRange}
                              onChange={(e) => setConcatRange(e.target.value)}
                            />
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '11px', color: 'var(--text-muted)', width: '55px' }}>Fichier :</span>
                            <input 
                              type="text" 
                              placeholder="Ex: Chapitre 1" 
                              className="input-range"
                              style={{ width: '100%', height: '30px', fontSize: '12px', background: 'rgba(255, 255, 255, 0.03)', border: '1px solid var(--panel-border)', borderRadius: '4px', padding: '0 8px', color: 'var(--text)' }}
                              value={concatFilename}
                              onChange={(e) => setConcatFilename(e.target.value)}
                            />
                          </div>
                        </div>
                        <button 
                          className="btn btn-secondary" 
                          style={{ width: '100%', borderColor: 'rgba(16, 185, 129, 0.4)', background: 'rgba(16, 185, 129, 0.05)' }}
                          onClick={() => runTool('concat', { ranges: concatRange, output_name: concatFilename })}
                          disabled={projectDetails.segments.length === 0}
                        >
                          <Music size={16} />
                          Compiler en Fichier MP3
                        </button>
                      </div>
                    </>
                  )}
                </div>

                {/* MP3 Audio Player for exported Audiobook */}
                {projectDetails.mp3_files && projectDetails.mp3_files.length > 0 && (
                  <div className="glass-panel control-card" style={{ background: 'rgba(16, 185, 129, 0.03)', padding: '16px', borderRadius: '16px', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                    <h4 style={{ color: 'var(--success)' }}>Fichiers MP3 Exportés ({projectDetails.mp3_files.length})</h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto', marginTop: '12px' }}>
                      {projectDetails.mp3_files.map(mp3 => (
                        <button 
                          key={mp3.filename}
                          className="btn btn-primary" 
                          style={{ width: '100%', background: 'rgba(16, 185, 129, 0.08)', border: '1px solid rgba(16, 185, 129, 0.2)', color: '#34d399', boxShadow: 'none', textAlign: 'left', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', padding: '6px 10px', textTransform: 'none' }}
                          onClick={() => setActiveAudio({
                            url: `${API_BASE}${mp3.url}?t=${new Date().getTime()}`,
                            title: selectedProject,
                            subtitle: mp3.filename
                          })}
                        >
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '85%' }}>{mp3.filename}</span>
                          <Play size={10} fill="currentColor" />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* CREATE NEW PROJECT MODAL */}
      {isCreateOpen && (
        <div className="modal-overlay">
          <div className="glass-panel modal-content">
            <div className="modal-header">
              <h3>Nouveau Projet d'Audiolivre</h3>
              <button className="btn-icon" onClick={() => setIsCreateOpen(false)}>×</button>
            </div>
            
            <form onSubmit={handleCreateProject}>
              <div className="form-group">
                <label>Nom du projet (ex: Stefan Zweig - Le Joueur d'échecs)</label>
                <input 
                  type="text" 
                  required 
                  className="input-text" 
                  value={newProjectName} 
                  onChange={(e) => setNewProjectName(e.target.value)} 
                  placeholder="Auteur - Titre"
                />
              </div>

              <div className="form-group">
                <label>Type de projet</label>
                <select 
                  className="input-text"
                  value={newProjectType}
                  onChange={(e) => setNewProjectType(e.target.value)}
                  style={{ width: '100%', background: 'rgba(0, 0, 0, 0.3)', color: '#fff' }}
                >
                  <option value="novel">Roman (mono narrateur)</option>
                  <option value="novel_multi">Roman (avec des personnages)</option>
                  <option value="theatre">Théâtre (plusieurs personnages)</option>
                </select>
              </div>

              <div style={{ background: 'rgba(255, 255, 255, 0.02)', border: '1px solid var(--panel-border)', padding: '12px', borderRadius: '8px', fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
                <HelpCircle size={14} style={{ display: 'inline', marginRight: '6px', verticalAlign: 'middle' }} />
                Une fois créé, placez votre fichier `.epub` original dans le dossier `Projects/Nom_Projet/book/` pour l'importer dans l'interface.
              </div>

              <div className="modal-actions">
                <button type="button" className="btn btn-secondary" onClick={() => setIsCreateOpen(false)}>Annuler</button>
                <button type="submit" className="btn btn-primary">Créer le projet</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* EDIT TEXT MODAL */}
      {editingSegment && (
        <div className="modal-overlay">
          <div className="glass-panel modal-content" style={isSplitting ? { maxWidth: '820px', width: '95%' } : {}}>
            <div className="modal-header">
              <h3>{isSplitting ? `Scinder le Segment n° ${editingSegment.num}` : `Éditer le Segment n° ${editingSegment.num}`}</h3>
              <button className="btn-icon" onClick={() => { setEditingSegment(null); setIsSplitting(false); }}>×</button>
            </div>
            
            {!isSplitting ? (
              <>
                <div className="form-group" style={{ marginBottom: '14px' }}>
                  <label style={{ display: 'block', marginBottom: '6px', fontSize: '13px' }}>
                    {isFr ? "Personnage (Rôle)" : "Character (Role)"}
                  </label>
                  <select 
                    className="input-select"
                    value={editProfileId}
                    onChange={(e) => setEditProfileId(e.target.value)}
                    style={{ 
                      width: '100%', 
                      padding: '8px 10px', 
                      background: 'rgba(0,0,0,0.3)', 
                      border: '1px solid rgba(255,255,255,0.15)', 
                      color: '#fff', 
                      borderRadius: '4px',
                      fontSize: '13px'
                    }}
                  >
                    {((projectDetails?.type === 'theatre')
                        ? (projectDetails?.custom_characters?.length ? projectDetails.custom_characters : (projectDetails?.characters || ['DIDAS']))
                        : ['Narrator', ...(projectDetails?.custom_characters || [])]
                      ).map(actor => (
                      <option key={actor} value={actor}>{actor}</option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label>Texte à synthétiser</label>
                  <textarea 
                    className="input-text" 
                    value={editText} 
                    onChange={(e) => setEditText(e.target.value)}
                  />
                </div>

                <div className="modal-actions" style={{ display: 'flex', justifyContent: 'space-between', width: '100%', gap: '8px' }}>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    {!editFromQc && (
                    <button
                      className="btn btn-secondary"
                      style={{ borderColor: 'rgba(167, 139, 250, 0.4)', background: 'rgba(167, 139, 250, 0.05)', color: '#a78bfa' }}
                      onClick={() => {
                        setIsSplitting(true);
                        setSplitPart1(JSON.stringify({
                          profile_id: editingSegment.profile_id || "Narrator",
                          text: editText,
                          generated_voice: editingSegment.generated_voice || undefined
                        }, null, 2));
                        setSplitPart2(JSON.stringify({
                          profile_id: "",
                          text: ""
                        }, null, 2));
                      }}
                    >
                      Scinder ce segment
                    </button>
                    )}
                    <button
                      className="btn btn-secondary"
                      style={{ borderColor: 'rgba(239, 68, 68, 0.5)', background: 'rgba(239, 68, 68, 0.08)', color: '#ef4444' }}
                      onClick={handleDeleteSegment}
                    >
                      Supprimer ce segment
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn btn-secondary" onClick={() => { setEditingSegment(null); setIsSplitting(false); }}>Annuler</button>
                    <button className="btn btn-primary" onClick={handleUpdateSegment}>Enregistrer les modifications</button>
                  </div>
                </div>
              </>
            ) : (
              <>
                <div style={{ display: 'flex', gap: '16px', marginTop: '12px', marginBottom: '16px' }}>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Segment n° {editingSegment.num} (JSON)</span>
                    <textarea 
                      className="input-text" 
                      style={{ fontFamily: 'Courier New, monospace', fontSize: '12px', height: '260px' }}
                      value={splitPart1}
                      onChange={(e) => setSplitPart1(e.target.value)}
                    />
                  </div>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Nouveau Segment n° {editingSegment.num + 1} (JSON)</span>
                    <textarea 
                      className="input-text" 
                      style={{ fontFamily: 'Courier New, monospace', fontSize: '12px', height: '260px' }}
                      value={splitPart2}
                      onChange={(e) => setSplitPart2(e.target.value)}
                    />
                  </div>
                </div>

                <div className="modal-actions">
                  <button className="btn btn-secondary" onClick={() => setIsSplitting(false)}>Annuler la scission</button>
                  <button className="btn btn-primary" onClick={handleSplitSegment}>Valider la scission</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* MENU CONTEXTUEL QC (clic droit sur une ligne du tableau QC) */}
      {qcContextMenu && (
        <>
          {/* Backdrop invisible : ferme le menu au clic ailleurs */}
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 9998 }}
            onClick={() => setQcContextMenu(null)}
            onContextMenu={(e) => { e.preventDefault(); setQcContextMenu(null); }}
          />
          <div
            className="glass-panel"
            style={{
              position: 'fixed',
              top: Math.min(qcContextMenu.y, window.innerHeight - 120),
              left: Math.min(qcContextMenu.x, window.innerWidth - 220),
              zIndex: 9999,
              padding: '10px',
              minWidth: '200px',
              borderRadius: '10px',
              boxShadow: '0 10px 40px rgba(0,0,0,0.5)',
            }}
          >
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '8px', paddingLeft: '2px' }}>
              {isFr ? `Segment n° ${qcContextMenu.num}` : `Segment #${qcContextMenu.num}`}
            </div>
            {qcContextMenu.mode === 'refuse' ? (
              <button
                className="btn btn-secondary"
                style={{
                  width: '100%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                  borderColor: 'rgba(239, 68, 68, 0.5)',
                  background: 'rgba(239, 68, 68, 0.10)',
                  color: '#f87171',
                }}
                onClick={() => { setSegmentQcStatus(qcContextMenu.num, 'force_refused'); setQcContextMenu(null); }}
                onContextMenu={(e) => { e.preventDefault(); setSegmentQcStatus(qcContextMenu.num, 'force_refused'); setQcContextMenu(null); }}
                title={isFr ? "Refuser la note et envoyer ce segment dans Quality Center" : "Refuse the score and send this segment to Quality Center"}
              >
                <Target size={14} />
                {isFr ? 'Refuser (→ Quality Center)' : 'Refuse (→ Quality Center)'}
              </button>
            ) : (
              <button
                className="btn btn-secondary"
                style={{
                  width: '100%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                  borderColor: 'rgba(251, 191, 36, 0.5)',
                  background: 'rgba(251, 191, 36, 0.10)',
                  color: '#fbbf24',
                }}
                onClick={() => { setSegmentQcStatus(qcContextMenu.num, 'forced'); setQcContextMenu(null); }}
                onContextMenu={(e) => { e.preventDefault(); setSegmentQcStatus(qcContextMenu.num, 'forced'); setQcContextMenu(null); }}
                title={isFr ? "Ignorer le seuil QC pour ce segment" : "Ignore QC threshold for this segment"}
              >
                <CheckCircle2 size={14} />
                {isFr ? 'Forcer (ignorer le seuil)' : 'Force (ignore threshold)'}
              </button>
            )}
          </div>
        </>
      )}


      {/* VERSION SELECTION MODAL */}
      {versionSelectionSegment && (
        <div className="modal-overlay">
          <div className="glass-panel modal-content version-selection-modal">
            <div className="modal-header">
              <h3>
                {isFr 
                  ? `Sélection de la Version - Segment n° ${versionSelectionSegment.num}`
                  : `Select Version - Segment #${versionSelectionSegment.num}`}
              </h3>
              <button className="btn-icon" onClick={handleCancelSelection}>×</button>
            </div>
            
            <div className="version-selection-body">
              <p className="version-selection-text-preview">
                "{versionSelectionSegment.text}"
              </p>
              
              <audio 
                ref={previewAudioRef} 
                style={{ display: 'none' }}
                onPlay={() => setPreviewIsPlaying(true)}
                onPause={() => setPreviewIsPlaying(false)}
                onEnded={() => {
                  setPreviewIsPlaying(false);
                  if (versionAutoplayActiveRef.current) {
                    const currentV = playingVersionRef.current;
                    const currentIndex = versionSelectionSegment.available_versions.indexOf(currentV);
                    if (currentIndex !== -1 && currentIndex < versionSelectionSegment.available_versions.length - 1) {
                      const nextVersion = versionSelectionSegment.available_versions[currentIndex + 1];
                      setSelectedVersion(nextVersion);
                      handlePlayPreview(nextVersion, true);
                    } else {
                      setVersionAutoplayActive(false);
                      setPlayingVersion(null);
                    }
                  } else {
                    setPlayingVersion(null);
                  }
                }}
              />

              <div className="version-list">
                {versionSelectionSegment.available_versions.map((version) => {
                  const isCurrentPlaying = playingVersion === version && previewIsPlaying;
                  const isSelected = selectedVersion === version;
                  
                  return (
                    <div 
                      key={version} 
                      className={`version-row ${isSelected ? 'selected' : ''}`}
                      onClick={() => setSelectedVersion(version)}
                    >
                      <label className="version-radio-container">
                        <input 
                          type="radio" 
                          name="selected_version" 
                          checked={isSelected}
                          onChange={() => setSelectedVersion(version)}
                        />
                        <span className="version-radio-checkmark"></span>
                        <span className="version-label-text">
                          {isFr ? `Version ${version.replace('v', '')}` : `Version ${version.replace('v', '')}`}
                        </span>
                      </label>

                      {(() => {
                        const vs = versionScores[version];
                        if (!vs) {
                          return <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>…</span>;
                        }
                        if (vs.score === null || vs.score === undefined) {
                          return <span style={{ fontSize: '11px', color: 'var(--text-muted)' }} title={vs.duration ? `${vs.duration.toFixed(1)}s` : ''}>n/a</span>;
                        }
                        return (
                          <span
                            style={{ ...getQcScoreStyle(vs.score), display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: '24px', minWidth: '40px' }}
                            title={isFr
                              ? `Score ${vs.score.toFixed(3)} · ${vs.duration ? vs.duration.toFixed(1) + 's' : ''} (réf ${vs.matched_ref_sec ?? '?'}s)`
                              : `Score ${vs.score.toFixed(3)} · ${vs.duration ? vs.duration.toFixed(1) + 's' : ''}`}
                          >
                            {Math.round(vs.score * 100)}
                          </span>
                        );
                      })()}

                      <button
                        className={`btn-icon ${isCurrentPlaying ? 'active' : ''}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          handlePlayPreview(version);
                        }}
                      >
                        {isCurrentPlaying ? <Pause size={14} /> : <Play size={14} fill="currentColor" />}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="modal-actions" style={{ marginTop: '24px', justifyContent: 'space-between', alignItems: 'center' }}>
              <button 
                className={`btn-icon ${versionAutoplayActive ? 'active' : ''}`}
                style={{ 
                  background: versionAutoplayActive ? 'rgba(139, 92, 246, 0.15)' : 'rgba(255, 255, 255, 0.03)',
                  borderColor: versionAutoplayActive ? 'var(--accent-color)' : 'var(--panel-border)',
                  color: versionAutoplayActive ? 'var(--accent-hover)' : 'var(--text-main)'
                }}
                onClick={toggleVersionAutoplay}
                title={isFr ? 'Lecture enchaînée de toutes les versions' : 'Autoplay all versions'}
              >
                {versionAutoplayActive ? <Pause size={14} /> : <Play size={14} fill="currentColor" />}
              </button>

              <div style={{ display: 'flex', gap: '12px' }}>
                <button className="btn btn-secondary" onClick={handleCancelSelection}>
                  {isFr ? 'Annuler' : 'Cancel'}
                </button>
                <button 
                  className="btn btn-primary" 
                  onClick={handleSelectVersion}
                  disabled={!selectedVersion}
                >
                  {isFr ? 'Valider et Nettoyer' : 'Confirm & Clean'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ERROR DETAIL MODAL */}
      {errorDetailMsg && (
        <div className="modal-overlay">
          <div className="glass-panel modal-content" style={{ maxWidth: '700px', width: '95%' }}>
            <div className="modal-header">
              <h3 style={{ color: '#ef4444', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span>⚠️ Erreur de Traitement</span>
              </h3>
              <button className="btn-icon" onClick={() => setErrorDetailMsg(null)}>×</button>
            </div>
            <div className="form-group" style={{ marginTop: '14px' }}>
              <p style={{ fontSize: '14px', color: 'var(--text-main)', marginBottom: '10px' }}>
                {isFr ? "Le système a rencontré l'anomalie suivante :" : "The system encountered the following error:"}
              </p>
              <textarea
                className="input-text"
                readOnly
                value={errorDetailMsg}
                style={{
                  fontFamily: 'Courier New, monospace',
                  fontSize: '12px',
                  height: '300px',
                  background: 'rgba(0, 0, 0, 0.4)',
                  color: '#f87171',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                  borderRadius: '6px',
                  padding: '10px',
                  width: '100%',
                  resize: 'vertical'
                }}
                onClick={(e) => e.target.select()}
                title={isFr ? "Cliquez pour tout sélectionner" : "Click to select all"}
              />
            </div>
            <div className="modal-actions" style={{ marginTop: '20px', justifyContent: 'flex-end' }}>
              <button 
                type="button" 
                className="btn btn-secondary" 
                onClick={() => {
                  navigator.clipboard.writeText(errorDetailMsg);
                  alert(isFr ? "Copié dans le presse-papiers !" : "Copied to clipboard!");
                }}
                style={{ borderColor: 'rgba(167, 139, 250, 0.4)', background: 'rgba(167, 139, 250, 0.05)', color: '#a78bfa' }}
              >
                {isFr ? "Copier le log" : "Copy Log"}
              </button>
              <button 
                type="button" 
                className="btn btn-primary" 
                onClick={() => setErrorDetailMsg(null)}
                style={{ background: 'rgba(239, 68, 68, 0.8)', borderColor: '#ef4444' }}
              >
                {isFr ? "Fermer" : "Close"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* FLOATING AUDIO PLAYER */}
      {activeAudio && (() => {
        const currentNum = activeAudio.num;
        const filteredSegments = projectDetails?.segments?.filter(
          seg => activeCharacterFilters.length === 0 || activeCharacterFilters.includes(seg.profile_id)
        ) || [];
        const hasPrevSeg = currentNum ? filteredSegments.some(s => s.num < currentNum && s.has_audio) : false;
        const hasNextSeg = currentNum ? filteredSegments.some(s => s.num > currentNum && s.has_audio) : false;
        const isActiveSegmentRegenerating = projectDetails?.active_segment === currentNum;
        
        return (
          <div className="sticky-audio-bar">
            <div className="player-info">
              <div className="player-title">{activeAudio.title}</div>
              <div className="player-subtitle">{activeAudio.subtitle}</div>
              {activeAudio.voice && <div className="player-voice">{activeAudio.voice}</div>}
            </div>
            
            {/* Previous Button */}
            <button
              className="btn-icon"
              onClick={handlePlayPrevSegment}
              disabled={!hasPrevSeg}
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                opacity: hasPrevSeg ? 1 : 0.4,
                cursor: hasPrevSeg ? 'pointer' : 'not-allowed',
                flexShrink: 0
              }}
              title={isFr ? "Segment précédent" : "Previous segment"}
            >
              <SkipBack size={14} fill={hasPrevSeg ? "currentColor" : "none"} />
            </button>

            {/* Target Button */}
            <button
              className={`btn-icon ${scrollTrackingActive ? 'active' : ''}`}
              onClick={() => {
                const next = !scrollTrackingActive;
                setScrollTrackingActive(next);
                localStorage.setItem('scrollTrackingActive', next.toString());
              }}
              style={{ 
                width: '32px', 
                height: '32px', 
                borderRadius: '50%', 
                background: scrollTrackingActive ? 'rgba(139, 92, 246, 0.25)' : 'rgba(255, 255, 255, 0.04)',
                borderColor: scrollTrackingActive ? '#8b5cf6' : 'var(--panel-border)',
                color: scrollTrackingActive ? '#c084fc' : 'var(--text-muted)',
                flexShrink: 0
              }}
              title={isFr ? "Tracker le segment en cours (cible)" : "Track current segment (target)"}
            >
              <Target size={16} />
            </button>

            {/* Next Button */}
            <button
              className="btn-icon"
              onClick={handlePlayNextSegment}
              disabled={!hasNextSeg}
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                opacity: hasNextSeg ? 1 : 0.4,
                cursor: hasNextSeg ? 'pointer' : 'not-allowed',
                flexShrink: 0
              }}
              title={isFr ? "Segment suivant" : "Next segment"}
            >
              <SkipForward size={14} fill={hasNextSeg ? "currentColor" : "none"} />
            </button>

            {/* Regenerate Button */}
            <button
              className="btn-icon"
              onClick={handleRegenerateActiveSegment}
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                flexShrink: 0
              }}
              title={isFr ? "Régénérer ce segment uniquement" : "Regenerate this segment only"}
            >
              <RefreshCw size={12} className={isActiveSegmentRegenerating ? "spin" : ""} />
            </button>

            <audio 
              ref={audioRef}
              controls 
              autoPlay 
              src={activeAudio.url} 
              key={activeAudio.url}
              onEnded={handleAudioEnded}
              onVolumeChange={handleVolumeChange}
            />
            {activeAudio.num && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <div className="autoplay-container" title={isFr ? "Lecture automatique enchaînée" : "Continuous autoplay"}>
                  <label className="switch">
                    <input 
                      type="checkbox" 
                      checked={autoplayEnabled} 
                      onChange={(e) => setAutoplayEnabled(e.target.checked)} 
                    />
                    <span className="slider">
                      <span className="slider-knob">
                        {autoplayEnabled ? (
                          /* Pause Icon SVG (YouTube style) */
                          <svg viewBox="0 0 24 24" style={{ width: '10px', height: '10px' }}>
                            <rect x="5" y="4" width="4" height="16" rx="1" fill="#8b5cf6" />
                            <rect x="15" y="4" width="4" height="16" rx="1" fill="#8b5cf6" />
                          </svg>
                        ) : (
                          /* Play Icon SVG (YouTube style) */
                          <svg viewBox="0 0 24 24" style={{ width: '10px', height: '10px', marginLeft: '1px' }}>
                            <polygon points="6,4 20,12 6,20" fill="#2d3748" />
                          </svg>
                        )}
                      </span>
                    </span>
                  </label>
                </div>

                {lastPlaySource === 'qc' && (
                  <button
                    className={`btn-icon ${qcAutoplayUseCentral ? 'active' : ''}`}
                    onClick={() => setQcAutoplayUseCentral(v => !v)}
                    title={isFr ? "Autoplay : utiliser l'ordre de la table centrale" : "Autoplay: use central table sequence"}
                    style={{
                      width: '28px',
                      height: '28px',
                      borderRadius: '50%',
                      background: qcAutoplayUseCentral ? 'rgba(139, 92, 246, 0.25)' : 'rgba(255, 255, 255, 0.05)',
                      borderColor: qcAutoplayUseCentral ? '#8b5cf6' : 'rgba(255, 255, 255, 0.1)',
                      color: qcAutoplayUseCentral ? '#c084fc' : 'rgba(255, 255, 255, 0.4)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer'
                    }}
                  >
                    <List size={14} />
                  </button>
                )}
              </div>
            )}
            <button className="btn-icon" style={{ borderRadius: '50%' }} onClick={() => setActiveAudio(null)}>×</button>
          </div>
        );
      })()}

      {/* MODALE IA & RÔLES (roman à personnages) */}
      {/* DRAWER IA & RÔLES : la colonne de gauche du projet reste visible/maîtresse.
          Le drawer s'ouvre à SA DROITE (décalage = padding 24 + col. 270 + gap 24 = 318px)
          et recouvre le centre + la droite. */}
      <div
        style={{
          position: 'fixed', top: 0, left: '318px', right: 0, bottom: 0, zIndex: 1000,
          background: '#12131a', borderLeft: '1px solid var(--panel-border)',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.45)',
          transform: isAiRolesOpen ? 'translateX(0)' : 'translateX(calc(-100% - 318px))',
          transition: 'transform 0.28s cubic-bezier(0.4,0,0.2,1)',
          pointerEvents: isAiRolesOpen ? 'auto' : 'none',
          display: 'flex', flexDirection: 'column'
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 22px', borderBottom: '1px solid var(--panel-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '18px' }}>🧠</span>
            <h2 style={{ margin: 0, fontSize: '17px' }}>{isFr ? 'IA & Rôles' : 'AI & Roles'}</h2>
            {aiRolesSource && (
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>— {aiRolesSource}</span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              className="btn btn-secondary"
              style={{ borderColor: 'rgba(16,185,129,0.5)', background: 'rgba(16,185,129,0.1)', color: '#34d399', fontWeight: 600 }}
              onClick={handleConcatValidated}
              title={isFr ? "Assembler tous les chunks validés en <projet>_parsed.txt (prêt pour la fabrication des segments)" : "Assemble all validated chunks into <project>_parsed.txt"}
            >
              📚 {isFr ? 'Assembler le livre' : 'Assemble book'}
            </button>
            <button className="btn-icon" style={{ borderRadius: '50%' }} onClick={() => setIsAiRolesOpen(false)}>×</button>
          </div>
        </div>

        {/* Barre d'outils : analyse IA par PLAGE de chunks (comme les tableaux TTS) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 22px', borderBottom: '1px solid var(--panel-border)', background: 'rgba(255,255,255,0.02)' }}>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 600 }}>{isFr ? 'Plage :' : 'Range:'}</span>
          <input
            type="text"
            value={aiRange}
            onChange={(e) => setAiRange(e.target.value)}
            placeholder={isFr ? "Ex : 1-10, 15, 20-30" : "e.g. 1-10, 15, 20-30"}
            style={{ width: '220px', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', borderRadius: '4px', fontSize: '12px', padding: '6px 10px' }}
          />
          <button
            className="btn btn-secondary"
            style={{ borderColor: 'rgba(139,92,246,0.5)', background: 'rgba(139,92,246,0.08)', color: '#a78bfa', fontWeight: 600 }}
            onClick={handleAnalyzeRange}
            disabled={ttsBusy || !aiRange.trim()}
            title={ttsBusy
              ? (isFr ? "Génération TTS en cours — analyse IA indisponible" : "TTS running — AI analysis unavailable")
              : (isFr ? "Analyser (IA) tous les chunks de la plage" : "AI-analyze all chunks in the range")}
          >
            {iaBusy
              ? `⏳ ${Object.values(aiJobs).filter(j => j && (j.status === 'queued' || j.status === 'running')).length} en file`
              : (isFr ? 'Analyser la plage (IA)' : 'Analyze range (AI)')}
          </button>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic' }}>
            {isFr ? "Chaque chunk passe par le LLM (peut être long)." : "Each chunk goes through the LLM (can be slow)."}
          </span>
        </div>

        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          {/* MILIEU : cadres AVANT (éditable) / APRÈS (lecture seule), comme l'atelier */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, borderRight: '1px solid var(--panel-border)', padding: '16px 20px' }}>
            {selectedAiChunk ? (
              <>
                {/* En-tête + actions du chunk */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', marginBottom: '12px' }}>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: '#34d399' }}>
                    {selectedAiChunk.filename} — {aiChunkEdit.length} {isFr ? 'caractères' : 'chars'}
                  </div>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      className="btn btn-secondary"
                      style={{ borderColor: 'rgba(52,211,153,0.6)', background: 'rgba(52,211,153,0.1)', color: '#34d399', fontWeight: 600 }}
                      onClick={() => handleMarkChunkNarrator(selectedAiChunk.index)}
                      disabled={chunkUnderIa(selectedAiChunk.index)}
                      title={isFr ? "Tout ce chunk = NARRATEUR (analyse IA non nécessaire)" : "This whole chunk = NARRATOR (no AI needed)"}
                    >
                      NARRATEUR
                    </button>
                    <button
                      className="btn btn-secondary"
                      style={{ borderColor: 'rgba(139,92,246,0.5)', background: 'rgba(139,92,246,0.08)', color: '#a78bfa', fontWeight: 600 }}
                      onClick={() => handleAnalyzeChunkAI(selectedAiChunk.index)}
                      disabled={ttsBusy || chunkUnderIa(selectedAiChunk.index)}
                      title={ttsBusy
                        ? (isFr ? "Génération TTS en cours — analyse IA indisponible" : "TTS running — AI analysis unavailable")
                        : (isFr ? "Analyse IA (WhoIsSpeaking / LM Studio) — attribue NARRATEUR + personnages" : "AI analysis — assigns NARRATOR + characters")}
                    >
                      {chunkUnderIa(selectedAiChunk.index)
                        ? (aiJobs[selectedAiChunk.index]?.status === 'running' ? (isFr ? '⏳ Analyse...' : '⏳ Analyzing...') : (isFr ? '⏳ En file...' : '⏳ Queued...'))
                        : (isFr ? 'Analyser (IA)' : 'Analyze (AI)')}
                    </button>
                  </div>
                </div>

                <div className="normalizer-text-comparison">
                  {/* AVANT (éditable, avec coloration de la ponctuation) */}
                  <div className="normalizer-editor-pane">
                    <span className="normalizer-pane-title">
                      <span>{isFr ? '📄 AVANT (texte du chunk — éditable)' : '📄 BEFORE (chunk text — editable)'}</span>
                    </span>
                    <div className="hl-editor">
                      <div
                        className="hl-backdrop"
                        ref={aiBackdropRef}
                        aria-hidden="true"
                        dangerouslySetInnerHTML={{ __html: highlightPunctuation(aiChunkEdit) }}
                      />
                      <textarea
                        className="hl-input"
                        spellCheck={false}
                        value={aiChunkEdit}
                        onChange={(e) => setAiChunkEdit(e.target.value)}
                        onScroll={(e) => {
                          if (aiBackdropRef.current) {
                            aiBackdropRef.current.scrollTop = e.target.scrollTop;
                            aiBackdropRef.current.scrollLeft = e.target.scrollLeft;
                          }
                        }}
                        placeholder={isFr ? "Contenu du chunk..." : "Chunk content..."}
                      />
                    </div>
                  </div>

                  {/* APRÈS (éditable) + enregistrement */}
                  <div className="normalizer-editor-pane">
                    <span className="normalizer-pane-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                      <span>{isFr ? '✨ APRÈS (rôles attribués — éditable)' : '✨ AFTER (roles assigned — editable)'}</span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        {selectedAiChunk.analyzed_filename && (
                          <span style={{ color: '#34d399', fontSize: '11px', textTransform: 'none' }}>{selectedAiChunk.analyzed_filename}</span>
                        )}
                        <button
                          className="btn btn-primary btn-sm"
                          style={{ padding: '4px 10px', fontSize: '11px', background: '#10b981', borderColor: '#10b981', color: '#fff', textTransform: 'none', letterSpacing: 'normal' }}
                          onClick={() => handleSaveAnalyzed(selectedAiChunk.index)}
                          disabled={aiAnalyzedSaving || !aiAnalyzedEdit.trim()}
                          title={isFr ? "Valider et enregistrer chunk_NNNNN_analyzed.txt" : "Save chunk_NNNNN_analyzed.txt"}
                        >
                          💾 {aiAnalyzedSaving ? (isFr ? 'Enregistrement...' : 'Saving...') : (isFr ? 'Enregistrer' : 'Save')}
                        </button>
                      </span>
                    </span>
                    <div className="hl-editor">
                      <div
                        className="hl-backdrop"
                        ref={aiAfterBackdropRef}
                        aria-hidden="true"
                        dangerouslySetInnerHTML={{ __html: highlightPunctuation(aiAnalyzedEdit) }}
                      />
                      <textarea
                        ref={aiAfterRef}
                        className="hl-input"
                        spellCheck={false}
                        value={aiAnalyzedEdit}
                        onChange={(e) => { setAiAnalyzedEdit(e.target.value); updateAiSuggestions(e.target); }}
                        onKeyDown={onAiAfterKeyDown}
                        onKeyUp={(e) => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) updateAiSuggestions(e.target); }}
                        onClick={(e) => updateAiSuggestions(e.target)}
                        onScroll={(e) => {
                          if (aiAfterBackdropRef.current) {
                            aiAfterBackdropRef.current.scrollTop = e.target.scrollTop;
                            aiAfterBackdropRef.current.scrollLeft = e.target.scrollLeft;
                          }
                        }}
                        onBlur={() => setTimeout(() => setAiSuggest(s => ({ ...s, open: false })), 150)}
                        placeholder={isFr ? "Clique sur NARRATEUR (ou Analyser IA) pour générer le résultat ici, puis corrige si besoin..." : "Click NARRATOR (or Analyze AI) to generate the result here, then edit if needed..."}
                      />
                      {aiSuggest.open && aiSuggest.items.length > 0 && (
                        <div
                          style={{
                            position: 'absolute', top: aiSuggest.top, left: aiSuggest.left, zIndex: 5,
                            minWidth: '160px', maxHeight: '200px', overflowY: 'auto',
                            background: '#1a1b26', border: '1px solid rgba(139,92,246,0.5)',
                            borderRadius: '6px', boxShadow: '0 8px 24px rgba(0,0,0,0.5)', padding: '4px'
                          }}
                        >
                          {aiSuggest.items.map((it, i) => (
                            <div
                              key={it}
                              onMouseDown={(e) => { e.preventDefault(); acceptAiSuggestion(it); }}
                              style={{
                                padding: '5px 10px', fontSize: '13px', borderRadius: '4px', cursor: 'pointer',
                                fontWeight: 600,
                                background: i === aiSuggest.index ? 'rgba(139,92,246,0.25)' : 'transparent',
                                color: i === aiSuggest.index ? '#c4b5fd' : 'var(--text)'
                              }}
                            >
                              {it}
                            </div>
                          ))}
                          <div style={{ padding: '3px 10px', fontSize: '10px', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                            {isFr ? '↑↓ choisir · Entrée valider' : '↑↓ select · Enter to accept'}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div style={{ color: 'var(--text-muted)', fontSize: '14px', lineHeight: 1.6, padding: '16px' }}>
                {isFr
                  ? "Sélectionnez un fichier dans la colonne de droite pour afficher son contenu ici."
                  : "Select a file in the right column to preview its content here."}
              </div>
            )}
          </div>

          {/* DROITE : liste des chunks numérotés */}
          <div style={{ width: '340px', display: 'flex', flexDirection: 'column', background: 'rgba(255,255,255,0.02)' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--panel-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-muted)' }}>
                {isFr ? 'Chunks à analyser' : 'Chunks to analyze'}
              </span>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{aiRolesChunks.length}</span>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
              {aiRolesLoading ? (
                <div style={{ padding: '16px', color: 'var(--text-muted)', fontSize: '13px' }}>{isFr ? 'Découpage en cours...' : 'Chunking...'}</div>
              ) : aiRolesChunks.length === 0 ? (
                <div style={{ padding: '16px', color: 'var(--text-muted)', fontSize: '13px' }}>{isFr ? 'Aucun chunk.' : 'No chunks.'}</div>
              ) : (
                aiRolesChunks.map((c) => (
                  <button
                    key={c.index}
                    onClick={() => handleSelectAiChunk(c.index)}
                    style={{
                      display: 'flex', width: '100%', textAlign: 'left', gap: '10px',
                      padding: '8px 10px', marginBottom: '4px', borderRadius: '6px',
                      border: '1px solid ' + (selectedAiChunk?.index === c.index ? 'rgba(16,185,129,0.6)' : 'transparent'),
                      background: selectedAiChunk?.index === c.index ? 'rgba(16,185,129,0.1)' : 'rgba(255,255,255,0.03)',
                      color: 'var(--text)', cursor: 'pointer'
                    }}
                  >
                    <span style={{ fontWeight: 700, color: '#34d399', minWidth: '28px' }}>{c.index}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>
                        {c.chars} {isFr ? 'car.' : 'chars'}{c.analyzed ? ' · ✓' : ''}
                      </span>
                      <span style={{ display: 'block', fontSize: '12px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: c.analyzed ? '#34d399' : undefined }}>{c.preview}</span>
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ATELIER DE NORMALISATION DRAWER */}
      <div
        className={`normalizer-drawer-overlay ${isNormalizerOpen ? 'open' : ''}`}
        onClick={() => setIsNormalizerOpen(false)}
      />
      <div className={`normalizer-drawer ${isNormalizerOpen ? 'open' : ''}`}>
        <div className="normalizer-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <h2 style={{ margin: 0 }}>
              {isFr ? "🎭 Atelier de Normalisation & Production" : "🎭 Normalization & Production Workspace"}
            </h2>
            
            {/* STEP INDICATOR BAR */}
            <div style={{ display: 'flex', gap: '14px', alignItems: 'center', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)', padding: '6px 16px', borderRadius: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', opacity: normalizerStep === 1 ? 1 : 0.4 }}>
                <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '20px', height: '20px', borderRadius: '50%', background: normalizerStep === 1 ? 'var(--accent-color)' : 'rgba(255,255,255,0.1)', color: '#fff', fontSize: '11px', fontWeight: 600 }}>1</span>
                <span style={{ fontSize: '12px', fontWeight: normalizerStep === 1 ? 600 : 400, color: normalizerStep === 1 ? '#a78bfa' : '#fff' }}>{isFr ? "Étape 1 : Normalisation" : "Step 1: Normalization"}</span>
              </div>
              {projectDetails?.type === 'theatre' ? (
                <>
                  <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.2)' }}>➔</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', opacity: normalizerStep === 2 ? 1 : 0.4 }}>
                    <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '20px', height: '20px', borderRadius: '50%', background: normalizerStep === 2 ? 'var(--accent-color)' : 'rgba(255,255,255,0.1)', color: '#fff', fontSize: '11px', fontWeight: 600 }}>2</span>
                    <span style={{ fontSize: '12px', fontWeight: normalizerStep === 2 ? 600 : 400, color: normalizerStep === 2 ? '#a78bfa' : '#fff' }}>{isFr ? "Étape 2 : Parsing" : "Step 2: Parsing"}</span>
                  </div>
                  <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.2)' }}>➔</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', opacity: normalizerStep === 3 ? 1 : 0.4 }}>
                    <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '20px', height: '20px', borderRadius: '50%', background: normalizerStep === 3 ? 'var(--accent-color)' : 'rgba(255,255,255,0.1)', color: '#fff', fontSize: '11px', fontWeight: 600 }}>3</span>
                    <span style={{ fontSize: '12px', fontWeight: normalizerStep === 3 ? 600 : 400, color: normalizerStep === 3 ? '#a78bfa' : '#fff' }}>{isFr ? "Étape 3 : Chunking" : "Step 3: Chunking"}</span>
                  </div>
                </>
              ) : projectDetails?.type === 'novel_multi' ? (
                <>
                  <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.2)' }}>➔</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', opacity: 0.7 }}>
                    <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '20px', height: '20px', borderRadius: '50%', background: 'rgba(255,255,255,0.1)', color: '#fff', fontSize: '11px', fontWeight: 600 }}>2</span>
                    <span style={{ fontSize: '12px', color: '#fff' }}>{isFr ? "Étape 2 : IA & Attribution des Rôles" : "Step 2: AI & Role Attribution"}</span>
                  </div>
                </>
              ) : (
                <>
                  <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.2)' }}>➔</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', opacity: 0.7 }}>
                    <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '20px', height: '20px', borderRadius: '50%', background: 'rgba(255,255,255,0.1)', color: '#fff', fontSize: '11px', fontWeight: 600 }}>2</span>
                    <span style={{ fontSize: '12px', color: '#fff' }}>{isFr ? "Étape 2 : Découpage" : "Step 2: Chunking"}</span>
                  </div>
                </>
              )}
            </div>
          </div>
          
          <button 
            className="btn btn-secondary" 
            style={{ 
              borderColor: 'var(--accent-color)', 
              background: 'rgba(139, 92, 246, 0.1)', 
              color: 'var(--accent-hover)',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '6px 16px',
              fontWeight: 600
            }}
            onClick={() => setIsNormalizerOpen(false)}
          >
            <ArrowLeft size={14} />
            {isFr ? "Retour au projet TTS" : "Back to TTS Project"}
          </button>
        </div>
        
        <div className="normalizer-layout">
          {normalizerStep === 1 && (
            <>
              {/* LEFT PANEL: CONFIGURATION */}
              <div className="normalizer-left-panel">
                {/* STEP INDICATOR SUBPANEL */}
                <div style={{ background: 'rgba(139, 92, 246, 0.08)', border: '1px solid rgba(139, 92, 246, 0.15)', padding: '12px 14px', borderRadius: '8px', fontSize: '12.5px', color: '#cbd5e1' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, color: '#a78bfa', marginBottom: '6px' }}>
                    <span style={{ background: 'var(--accent-color)', width: '8px', height: '8px', borderRadius: '50%' }} />
                    {isFr ? "📍 Étape 1 : Normalisation" : "📍 Step 1: Normalization"}
                  </div>
                  {projectDetails?.type === 'theatre' ? (
                    isFr 
                      ? "Nettoyez et formatez la pièce brute. Cliquez sur 'Valider ==> Étape 2' pour générer le fichier normalisé permanent et exécuter automatiquement le parsing."
                      : "Clean up and format the raw play script. Click 'Validate ==> Step 2' to write the normalized file and automatically execute play parsing."
                  ) : projectDetails?.type === 'novel_multi' ? (
                    isFr 
                      ? "Nettoyez et formatez le texte brut du roman. Cliquez sur 'Valider & Associer les Rôles' pour générer le fichier normalisé permanent et ouvrir l'attribution des rôles par l'IA."
                      : "Clean up and format the raw novel text. Click 'Validate & Assign Roles' to write the normalized file and open AI role attribution."
                  ) : (
                    isFr 
                      ? "Nettoyez et formatez le texte brut du roman. Cliquez sur 'Valider & Créer les segments' pour générer le fichier normalisé permanent et le découper automatiquement."
                      : "Clean up and format the raw novel text. Click 'Validate & Create segments' to write the normalized file and automatically segment it."
                  )}
                </div>

                {/* PRESETS CONFIGURATION SECTION */}
                <div className="normalizer-section" style={{ borderLeft: '3px solid #8b5cf6', background: 'rgba(139, 92, 246, 0.03)' }}>
                  <h4 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '0 0 10px 0' }}>
                    <span>{isFr ? "📋 Configurations & Presets" : "📋 Configurations & Presets"}</span>
                  </h4>
                  
                  <div style={{ display: 'flex', gap: '8px', marginBottom: '10px' }}>
                    <div style={{ flex: 1 }}>
                      <select
                        value={selectedPresetName}
                        onChange={(e) => {
                          const name = e.target.value;
                          setSelectedPresetName(name);
                          if (name) {
                            const found = normalizationPresets.find(p => p.name === name);
                            if (found) {
                              applyPreset(found.config);
                            }
                          }
                        }}
                        style={{ 
                          width: '100%', 
                          padding: '8px 10px', 
                          background: 'rgba(0,0,0,0.3)', 
                          border: '1px solid rgba(255,255,255,0.1)', 
                          color: '#fff', 
                          borderRadius: '4px',
                          fontSize: '12px'
                        }}
                      >
                        <option value="">{isFr ? "-- Choisir une configuration --" : "-- Choose a configuration --"}</option>
                        {normalizationPresets.map(preset => (
                          <option key={preset.name} value={preset.name}>
                            {preset.name}
                          </option>
                        ))}
                      </select>
                    </div>

                    {selectedPresetName && (
                      <button
                        onClick={(e) => handleDeletePreset(selectedPresetName, e)}
                        className="btn btn-secondary"
                        style={{ 
                          padding: '6px 10px', 
                          background: 'rgba(239, 68, 68, 0.1)', 
                          borderColor: 'rgba(239, 68, 68, 0.3)', 
                          color: '#ef4444',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          borderRadius: '4px'
                        }}
                        title={isFr ? "Supprimer ce preset" : "Delete this preset"}
                      >
                        🗑️
                      </button>
                    )}
                  </div>

                  {showSavePresetModal ? (
                    <div style={{ background: 'rgba(0,0,0,0.2)', padding: '10px', borderRadius: '4px', border: '1px solid rgba(255,255,255,0.05)' }}>
                      <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>
                        {isFr ? "Nom de la configuration" : "Configuration Name"}
                      </label>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        <input
                          type="text"
                          value={newPresetName}
                          onChange={(e) => setNewPresetName(e.target.value)}
                          placeholder={isFr ? "ex: Molière Standard" : "e.g. Moliere Standard"}
                          style={{
                            flex: 1,
                            padding: '4px 8px',
                            fontSize: '12px',
                            background: 'rgba(0,0,0,0.3)',
                            border: '1px solid rgba(255,255,255,0.15)',
                            borderRadius: '4px',
                            color: '#fff'
                          }}
                        />
                        <button
                          onClick={handleSavePreset}
                          className="btn btn-primary"
                          style={{ padding: '4px 10px', fontSize: '11px', background: '#8b5cf6', borderColor: '#8b5cf6' }}
                        >
                          {isFr ? "Sauver" : "Save"}
                        </button>
                        <button
                          onClick={() => {
                            setShowSavePresetModal(false);
                            setNewPresetName('');
                          }}
                          className="btn btn-secondary"
                          style={{ padding: '4px 8px', fontSize: '11px' }}
                        >
                          {isFr ? "Annuler" : "Cancel"}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      onClick={() => setShowSavePresetModal(true)}
                      className="btn btn-secondary"
                      style={{ 
                        width: '100%', 
                        fontSize: '11.5px', 
                        padding: '6px 12px',
                        background: 'rgba(139, 92, 246, 0.1)',
                        borderColor: 'rgba(139, 92, 246, 0.3)',
                        color: '#c084fc',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '6px',
                        fontWeight: 600
                      }}
                    >
                      💾 {isFr ? "Sauvegarder cette configuration" : "Save this configuration"}
                    </button>
                  )}
                </div>

                <div className="normalizer-section">
                  <h4>{isFr ? "⚙️ Nettoyage de Base" : "⚙️ Base Cleanup"}</h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    <label className="checkbox-container" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px' }}>
                      <input 
                        type="checkbox" 
                        checked={removePageNumbers} 
                        onChange={(e) => {
                          setRemovePageNumbers(e.target.checked);
                          triggerReformat(pieceStyle, e.target.checked);
                        }}
                        style={{ cursor: 'pointer' }}
                      />
                      <span>{isFr ? "Supprimer les numéros de page (ex: 3, 4. La conjecture)" : "Remove page numbers (e.g. 3, 4. Conjecture)"}</span>
                    </label>
                    
                    <label className="checkbox-container" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px', opacity: 0.6 }}>
                      <input 
                        type="checkbox" 
                        checked={true} 
                        disabled 
                        style={{ cursor: 'not-allowed' }}
                      />
                      <span>{isFr ? "Uniformiser les lignes vides (lignes blanches multiples)" : "Collapse consecutive empty lines"}</span>
                    </label>
                  </div>
                </div>

                {projectDetails?.type === 'theatre' && (
                <div className="normalizer-section">
                  <h4>{isFr ? "🎭 Formatage des Rôles" : "🎭 Roles Formatting"}</h4>
                  <div className="form-group" style={{ marginBottom: '14px' }}>
                    <label style={{ fontSize: '12.5px', color: 'var(--text-muted)', marginBottom: '6px', display: 'block' }}>
                      {isFr ? "Style de Pièce (Préréglage)" : "Play Style (Preset)"}
                    </label>
                    <select 
                      value={pieceStyle} 
                      onChange={(e) => {
                        setPieceStyle(e.target.value);
                        triggerReformat(e.target.value, removePageNumbers);
                      }}
                      style={{ width: '100%', padding: '6px 10px', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', borderRadius: '4px' }}
                    >
                      {playStyles.map(style => (
                        <option key={style.id} value={style.id}>{style.name}</option>
                      ))}
                    </select>
                  </div>

                  <div style={{ display: 'flex', gap: '8px', marginTop: '8px', marginBottom: '14px' }}>
                    <button
                      onClick={() => {
                        setEditingStyle({ id: 'style_' + Date.now(), name: '', pattern: '^(\\s*__ACTORS__\\s*(?:\\(([^)]*)\\))?\\s*[-–\\—]\\s*(.*)$)', system: false });
                        setStyleEditorError('');
                        setShowStyleEditor(true);
                      }}
                      className="btn btn-secondary btn-sm"
                      style={{ flex: 1, padding: '4px 8px', fontSize: '11px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px' }}
                    >
                      ➕ {isFr ? "Nouveau Style" : "Add Custom Style"}
                    </button>
                    {(() => {
                      const current = playStyles.find(s => s.id === pieceStyle);
                      if (current && !current.system) {
                        return (
                          <>
                            <button
                              onClick={() => {
                                setEditingStyle({ ...current });
                                setStyleEditorError('');
                                setShowStyleEditor(true);
                              }}
                              className="btn btn-secondary btn-sm"
                              style={{ padding: '4px 8px', fontSize: '11px' }}
                            >
                              ✏️ {isFr ? "Modifier" : "Edit"}
                            </button>
                            <button
                              onClick={() => handleDeletePlayStyle(current.id)}
                              className="btn btn-danger btn-sm"
                              style={{ padding: '4px 8px', fontSize: '11px', backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid rgba(239, 68, 68, 0.4)', color: '#fca5a5' }}
                            >
                              🗑️ {isFr ? "Supprimer" : "Delete"}
                            </button>
                          </>
                        );
                      }
                      return null;
                    })()}
                  </div>

                  {showStyleEditor && editingStyle && (
                    <div style={{ 
                      marginTop: '10px',
                      marginBottom: '14px',
                      background: 'rgba(0,0,0,0.2)', 
                      padding: '12px', 
                      borderRadius: '6px', 
                      border: '1px solid rgba(255,255,255,0.05)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px'
                    }}>
                      <h5 style={{ margin: '0 0 4px 0', fontSize: '12px', color: '#c084fc' }}>
                        {editingStyle.system ? (isFr ? "Modifier Style Système" : "Edit System Style") : (isFr ? "Style Personnalisé" : "Custom Style")}
                      </h5>
                      
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>
                          {isFr ? "Nom du Style" : "Style Name"}
                        </label>
                        <input
                          type="text"
                          value={editingStyle.name}
                          onChange={(e) => setEditingStyle({ ...editingStyle, name: e.target.value })}
                          placeholder={isFr ? "ex: Style Deux Groupes" : "e.g. Two-group Style"}
                          style={{
                            width: '100%',
                            padding: '6px 8px',
                            fontSize: '12px',
                            background: 'rgba(0,0,0,0.3)',
                            border: '1px solid rgba(255,255,255,0.15)',
                            borderRadius: '4px',
                            color: '#fff',
                            boxSizing: 'border-box'
                          }}
                        />
                      </div>

                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>
                          {isFr ? "Expression Régulière (Regex)" : "Regular Expression (Regex)"}
                        </label>
                        <input
                          type="text"
                          value={editingStyle.pattern}
                          onChange={(e) => setEditingStyle({ ...editingStyle, pattern: e.target.value })}
                          placeholder="^__ACTORS__\s*(?:\(([^)]*)\))?\s*[-–\—]\s*(.*)$"
                          style={{
                            width: '100%',
                            padding: '6px 8px',
                            fontSize: '12px',
                            fontFamily: 'monospace',
                            background: 'rgba(0,0,0,0.3)',
                            border: '1px solid rgba(255,255,255,0.15)',
                            borderRadius: '4px',
                            color: '#fff',
                            boxSizing: 'border-box'
                          }}
                        />
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '4px', display: 'block', lineHeight: '1.2' }}>
                          {isFr ? "Utilisez __ACTORS__ pour la liste des personnages." : "Use __ACTORS__ for the character list."}
                        </span>
                      </div>

                      {styleEditorError && (
                        <div style={{ color: '#ef4444', fontSize: '11px', marginTop: '4px' }}>
                          ⚠️ {styleEditorError}
                        </div>
                      )}

                      <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
                        <button
                          onClick={handleSavePlayStyle}
                          className="btn btn-primary"
                          style={{ flex: 1, padding: '4px 10px', fontSize: '11px', background: '#8b5cf6', borderColor: '#8b5cf6' }}
                        >
                          {isFr ? "Sauver" : "Save"}
                        </button>
                        <button
                          onClick={() => {
                            setShowStyleEditor(false);
                            setEditingStyle(null);
                            setStyleEditorError('');
                          }}
                          className="btn btn-secondary"
                          style={{ padding: '4px 10px', fontSize: '11px' }}
                        >
                          {isFr ? "Annuler" : "Cancel"}
                        </button>
                      </div>
                    </div>
                  )}

                  <div style={{ background: 'rgba(167, 139, 250, 0.05)', padding: '10px', borderRadius: '6px', border: '1px solid rgba(167, 139, 250, 0.15)', fontSize: '12px', color: '#c084fc' }}>
                    <strong>Regex générée :</strong>
                    <pre style={{ margin: '6px 0 0 0', background: 'rgba(0,0,0,0.3)', padding: '6px', borderRadius: '4px', fontFamily: 'monospace', overflowX: 'auto', fontSize: '11px', color: '#cbd5e1' }}>
                      {(() => {
                        const styleObj = playStyles.find(s => s.id === pieceStyle);
                        if (styleObj) {
                          const pattern = styleObj.pattern || '';
                          const currentActors = (projectDetails?.custom_characters || projectDetails?.characters || [])
                            .filter(a => a.toUpperCase() !== 'DIDAS')
                            .slice(0, 3)
                            .map(a => a.toUpperCase());
                          
                          const actorsDisplay = currentActors.length > 0 
                            ? `(${currentActors.join('|')}|...)`
                            : '(ACTEUR_1|ACTEUR_2|...)';
                          return '/' + pattern.replace('__ACTORS__', actorsDisplay) + '/i';
                        }
                        return '';
                      })()}
                    </pre>
                  </div>
                </div>
                )}

                <div className="normalizer-section">
                  <h4>{isFr ? "🔍 Recherche / Remplacement" : "🔍 Search & Replace"}</h4>
                  
                  {/* RULES LIST */}
                  {normalizationRules.length > 0 && (
                    <div style={{ 
                      maxHeight: '180px', 
                      overflowY: 'auto', 
                      background: 'rgba(0,0,0,0.2)', 
                      borderRadius: '6px', 
                      padding: '8px', 
                      marginBottom: '12px',
                      border: '1px solid rgba(255,255,255,0.05)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '6px'
                    }}>
                      {normalizationRules.map((rule) => (
                        <div key={rule.id} style={{ 
                          display: 'flex', 
                          alignItems: 'center', 
                          justifyContent: 'space-between', 
                          gap: '6px',
                          padding: '4px 6px',
                          borderRadius: '4px',
                          background: rule.active ? 'rgba(167, 139, 250, 0.05)' : 'rgba(255,255,255,0.02)',
                          border: '1px solid ' + (rule.active ? 'rgba(167, 139, 250, 0.1)' : 'transparent')
                        }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', overflow: 'hidden', flex: 1 }}>
                            <input 
                              type="checkbox" 
                              checked={rule.active} 
                              onChange={() => handleToggleRule(rule.id)}
                              style={{ cursor: 'pointer', accentColor: 'var(--accent-color)' }}
                            />
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '11px' }}>
                              <span style={{ color: rule.active ? '#a78bfa' : 'var(--text-muted)', fontFamily: 'monospace', fontWeight: 600 }}>{rule.search}</span>
                              <span style={{ color: 'var(--text-muted)', margin: '0 4px' }}>→</span>
                              <span style={{ color: '#10b981', fontFamily: 'monospace' }}>
                                {rule.replace === '' ? (isFr ? '(suppression)' : '(delete)') : rule.replace}
                              </span>
                            </div>
                          </div>
                          <button 
                            onClick={() => handleDeleteRule(rule.id)}
                            style={{ 
                              background: 'none', 
                              border: 'none', 
                              color: 'rgba(239, 68, 68, 0.7)', 
                              cursor: 'pointer', 
                              fontSize: '14px',
                              padding: '0 4px',
                              display: 'flex',
                              alignItems: 'center'
                            }}
                            title={isFr ? "Supprimer la règle" : "Delete rule"}
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* ADD NEW RULE FORM */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '10px', background: 'rgba(255,255,255,0.02)', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.03)' }}>
                    <div className="form-group" style={{ marginBottom: '0' }}>
                      <label style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px', display: 'block' }}>
                        {isFr ? "Rechercher (Texte ou Regex /motif/flags)" : "Search (Text or Regex /pattern/flags)"}
                      </label>
                      <input 
                        type="text" 
                        className="input-text" 
                        value={newSearchRule}
                        onChange={(e) => setNewSearchRule(e.target.value)}
                        placeholder={isFr ? "Ex: /page\\s+\\d+/i" : "e.g. /page\\s+\\d+/i"}
                        style={{ width: '100%', fontSize: '11.5px', padding: '5px 8px', background: 'rgba(0,0,0,0.3)' }}
                      />
                    </div>
                    
                    <div className="form-group" style={{ marginBottom: '0' }}>
                      <label style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px', display: 'block' }}>
                        {isFr ? "Remplacer par (laisser vide pour supprimer)" : "Replace with (leave empty to delete)"}
                      </label>
                      <input 
                        type="text" 
                        className="input-text" 
                        value={newReplaceRule}
                        onChange={(e) => setNewReplaceRule(e.target.value)}
                        placeholder={isFr ? "Remplacement..." : "Replacement..."}
                        style={{ width: '100%', fontSize: '11.5px', padding: '5px 8px', background: 'rgba(0,0,0,0.3)' }}
                      />
                    </div>

                    <button 
                      type="button"
                      className="btn btn-secondary"
                      onClick={handleAddRule}
                      disabled={!newSearchRule.trim()}
                      style={{ 
                        fontSize: '11px', 
                        padding: '5px', 
                        marginTop: '4px',
                        background: newSearchRule.trim() ? 'rgba(167, 139, 250, 0.15)' : 'rgba(255,255,255,0.03)',
                        borderColor: newSearchRule.trim() ? '#a78bfa' : 'rgba(255,255,255,0.1)',
                        color: newSearchRule.trim() ? '#fff' : 'var(--text-muted)'
                      }}
                    >
                      {isFr ? "+ Ajouter à la liste" : "+ Add to list"}
                    </button>
                  </div>
                </div>

                <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <button 
                    className="btn btn-secondary"
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', background: 'rgba(255, 255, 255, 0.05)', border: '1px solid rgba(255,255,255,0.1)' }}
                    onClick={() => handleTestNormalizationRules()}
                    disabled={normalizerLoading}
                  >
                    <RefreshCw size={14} className={normalizerLoading ? "spin" : ""} />
                    {isFr ? "Tester les règles" : "Test Rules"}
                  </button>
                  
                  <button
                    className="btn btn-primary"
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', background: 'var(--accent-color)', borderColor: 'var(--accent-hover)' }}
                    onClick={() => {
                      if (projectDetails?.type === 'theatre') {
                        handleNextStepFromStep1();
                      } else if (projectDetails?.type === 'novel_multi') {
                        handleNovelNormalizeAndGoToAiRoles();
                      } else {
                        handleNovelNormalizeAndSplit();
                      }
                    }}
                    disabled={normalizerLoading}
                  >
                    {normalizerLoading && <RefreshCw size={14} className="spin" />}
                    {projectDetails?.type === 'theatre'
                      ? (isFr ? "Valider ==> Étape 2" : "Validate ==> Step 2")
                      : projectDetails?.type === 'novel_multi'
                      ? (isFr ? "Valider & Associer les Rôles" : "Validate & Assign Roles")
                      : (isFr ? "Valider & Créer les segments" : "Validate & Create segments")}
                  </button>
                </div>
              </div>

              {/* RIGHT PANEL: COMPARATIVE VISUALIZATION */}
              <div className="normalizer-right-panel">
                <div className="normalizer-text-comparison">
                  <div className="normalizer-editor-pane">
                    <span className="normalizer-pane-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                        <span style={{ fontWeight: 600, color: '#fff' }}>{isFr ? "📝 Avant (Texte Formaté v_n - Éditable)" : "📝 Before (Formatted Text v_n - Editable)"}</span>
                        <span style={{ color: 'var(--text-muted)', fontSize: '11px', textTransform: 'none' }}>
                          {isFr ? "Modifiez ce texte formaté pour faire des corrections ou tester des règles" : "Modify this formatted text to make corrections or test rules"}
                        </span>
                      </div>
                      <button
                        className="btn btn-primary btn-sm"
                        style={{
                          padding: '4px 10px',
                          fontSize: '11px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          background: '#8b5cf6',
                          borderColor: '#8b5cf6',
                          color: '#fff',
                          textTransform: 'none',
                          letterSpacing: 'normal'
                        }}
                        onClick={handleSaveSourceText}
                        disabled={normalizerLoading}
                        title={isFr ? "Enregistrer les modifications dans le fichier source" : "Save changes to the source file"}
                      >
                        💾 {isFr ? "Enregistrer" : "Save"}
                      </button>
                    </span>
                    <textarea 
                      className="normalizer-textarea"
                      value={normalizerTopText}
                      onChange={(e) => {
                        setNormalizerTopText(e.target.value);
                        if (!newSearchRule.trim()) {
                          setNormalizerBottomText(e.target.value);
                        }
                      }}
                      placeholder={isFr ? "Le texte formaté apparaîtra ici..." : "Formatted text will appear here..."}
                    />
                  </div>
                  
                  <div className="normalizer-editor-pane">
                    <span className="normalizer-pane-title">
                      <span>{isFr ? "✨ Après (Aperçu de la Règle v_n+1 - Lecture Seule)" : "✨ After (Rule Preview v_n+1 - Read Only)"}</span>
                      {normalizerLoading && <span style={{ color: '#f59e0b', fontSize: '11px' }}>{isFr ? "Mise à jour..." : "Updating..."}</span>}
                    </span>
                    <textarea 
                      className="normalizer-textarea"
                      readOnly
                      value={normalizerBottomText}
                      placeholder={isFr ? "L'aperçu avec la nouvelle règle apparaîtra ici..." : "The preview with the new rule will appear here..."}
                    />
                  </div>
                </div>
              </div>
            </>
          )}

          {normalizerStep === 2 && (
            <>
              {/* LEFT PANEL: CONFIGURATION */}
              <div className="normalizer-left-panel">
                {/* STEP INDICATOR SUBPANEL */}
                <div style={{ background: 'rgba(16, 185, 129, 0.08)', border: '1px solid rgba(16, 185, 129, 0.2)', padding: '12px 14px', borderRadius: '8px', fontSize: '12.5px', color: '#cbd5e1' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, color: '#34d399', marginBottom: '6px' }}>
                    <span style={{ background: '#10b981', width: '8px', height: '8px', borderRadius: '50%' }} />
                    {isFr ? "📍 Étape 2 : Parsing de la Pièce" : "📍 Step 2: Play Parsing"}
                  </div>
                  {isFr 
                    ? "Le script parse_theatre.py a été exécuté avec succès. Il a séparé et structuré les locuteurs et répliques. Valisez pour lancer l'Étape 3 (Coupe des répliques trop longues)."
                    : "The parse_theatre.py script ran successfully. It separated speakers from staging directions. Validate to proceed to Step 3 (Long lines cut)."}
                </div>

                <div className="normalizer-section">
                  <h4 style={{ color: '#34d399' }}>{isFr ? "📁 Infos Fichier" : "📁 File Info"}</h4>
                  <div style={{ fontSize: '13px', lineHeight: '1.6' }}>
                    <div><strong>{isFr ? "Fichier Parsé :" : "Parsed File:"}</strong></div>
                    <div style={{ fontFamily: 'monospace', color: '#a7f3d0', wordBreak: 'break-all', margin: '4px 0 10px 0', background: 'rgba(0,0,0,0.2)', padding: '6px', borderRadius: '4px' }}>
                      {parsedFilename || "N/A"}
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {isFr 
                        ? "Ce fichier contient l'alternance structurée de personnages (lignes majuscules) et de leurs répliques." 
                        : "This file contains the structured alternation of characters (capitalized lines) and their lines."}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <button 
                    className="btn btn-secondary"
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}
                    onClick={() => setNormalizerStep(1)}
                    disabled={normalizerLoading}
                  >
                    <ArrowLeft size={14} />
                    {isFr ? "Retour à l'Étape 1" : "Back to Step 1"}
                  </button>
                  
                  <button 
                    className="btn btn-primary" 
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', background: 'var(--accent-color)', borderColor: 'var(--accent-hover)' }}
                    onClick={() => handleNextStepFromStep2()}
                    disabled={normalizerLoading}
                  >
                    {normalizerLoading && <RefreshCw size={14} className="spin" />}
                    {isFr ? "Valider ==> Étape 3" : "Validate ==> Step 3"}
                  </button>
                </div>
              </div>

              {/* RIGHT PANEL: SINGLE VIEW */}
              <div className="normalizer-right-panel">
                <div className="normalizer-text-comparison">
                  <div className="normalizer-editor-pane" style={{ height: '100%' }}>
                    <span className="normalizer-pane-title">
                      <span>{isFr ? `📄 Résultat du Parsing (${parsedFilename})` : `📄 Parsing Result (${parsedFilename})`}</span>
                    </span>
                    <textarea 
                      className="normalizer-textarea"
                      readOnly
                      value={parsedTextSample || (isFr ? "[Aucun contenu]" : "[No content]")}
                      style={{ fontFamily: 'monospace', fontSize: '13px' }}
                    />
                  </div>
                </div>
              </div>
            </>
          )}

          {normalizerStep === 3 && (
            <>
              {/* LEFT PANEL: CONFIGURATION */}
              <div className="normalizer-left-panel">
                {/* STEP INDICATOR SUBPANEL */}
                <div style={{ background: 'rgba(167, 139, 250, 0.08)', border: '1px solid rgba(167, 139, 250, 0.2)', padding: '12px 14px', borderRadius: '8px', fontSize: '12.5px', color: '#cbd5e1' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, color: '#c084fc', marginBottom: '6px' }}>
                    <span style={{ background: 'var(--accent-color)', width: '8px', height: '8px', borderRadius: '50%' }} />
                    {isFr ? "📍 Étape 3 : Chunking" : "📍 Step 3: Chunking"}
                  </div>
                  {isFr 
                    ? "Le script chunker_theatre.py a été exécuté. Il a découpé les phrases trop longues. Cliquez sur 'Créer les segments' pour finaliser et retourner au projet."
                    : "The chunker_theatre.py script ran. It cut overly long sentences. Click 'Create segments' to finalize and return to the project dashboard."}
                </div>

                <div className="normalizer-section">
                  <h4 style={{ color: '#c084fc' }}>{isFr ? "📁 Infos Fichier" : "📁 File Info"}</h4>
                  <div style={{ fontSize: '13px', lineHeight: '1.6' }}>
                    <div><strong>{isFr ? "Fichier Chunké :" : "Chunked File:"}</strong></div>
                    <div style={{ fontFamily: 'monospace', color: '#c084fc', wordBreak: 'break-all', margin: '4px 0 10px 0', background: 'rgba(0,0,0,0.2)', padding: '6px', borderRadius: '4px' }}>
                      {chunkedFilename || "N/A"}
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {isFr 
                        ? "Ce fichier contient les répliques découpées prêtes pour la synthèse vocale segmentée." 
                        : "This file contains the split lines ready for segmented text-to-speech."}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <button 
                    className="btn btn-secondary"
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}
                    onClick={() => setNormalizerStep(2)}
                    disabled={normalizerLoading}
                  >
                    <ArrowLeft size={14} />
                    {isFr ? "Retour à l'Étape 2" : "Back to Step 2"}
                  </button>
                  
                  <button 
                    className="btn btn-primary" 
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', background: '#10b981', borderColor: '#059669' }}
                    onClick={() => handleFinishFromStep3()}
                    disabled={normalizerLoading}
                  >
                    {normalizerLoading && <RefreshCw size={14} className="spin" />}
                    {isFr ? "Créer les segments (Étape 4)" : "Create Segments (Step 4)"}
                  </button>
                </div>
              </div>

              {/* RIGHT PANEL: SINGLE VIEW */}
              <div className="normalizer-right-panel">
                <div className="normalizer-text-comparison">
                  <div className="normalizer-editor-pane" style={{ height: '100%' }}>
                    <span className="normalizer-pane-title">
                      <span>{isFr ? `📄 Résultat du Chunking (${chunkedFilename})` : `📄 Chunking Result (${chunkedFilename})`}</span>
                    </span>
                    <textarea 
                      className="normalizer-textarea"
                      readOnly
                      value={chunkedTextSample || (isFr ? "[Aucun contenu]" : "[No content]")}
                      style={{ fontFamily: 'monospace', fontSize: '13px' }}
                    />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
