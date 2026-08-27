import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpen, ChevronLeft, Clock, FileText, Pause, Play, ShieldCheck, Trash2, Upload, X } from 'lucide-react'
import './index.css'
import { deleteDocument, getDocuments, saveDocument, type StoredDocument } from './storage'
import { parseDocument, SUPPORTED_EXTENSIONS } from './textExtraction'

type AppView = 'LIBRARY' | 'VIEWER'
const wordsFrom = (text: string) => text.split(/\s+/u).filter(Boolean)
const bookmarkKey = (id: string) => `fast-reader:bookmark:${id}`

function App() {
  const [view, setView] = useState<AppView>('LIBRARY')
  const [documents, setDocuments] = useState<StoredDocument[]>([])
  const [currentDocument, setCurrentDocument] = useState<StoredDocument | null>(null)
  const [isReaderActive, setIsReaderActive] = useState(false)
  const [readerIndex, setReaderIndex] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [isDragging, setIsDragging] = useState(false)
  const [error, setError] = useState('')

  const refreshLibrary = async () => setDocuments(await getDocuments())

  useEffect(() => {
    getDocuments()
      .then(setDocuments)
      .catch(() => setError('Your browser could not open the local library.'))
      .finally(() => setIsLoading(false))
  }, [])

  const importFile = async (file?: File) => {
    if (!file) return
    setError('')
    setIsLoading(true)
    try {
      const text = await parseDocument(file)
      const words = wordsFrom(text)
      if (!words.length) throw new Error('No readable text was found in this document.')
      await saveDocument({
        id: crypto.randomUUID(), name: file.name, type: file.type, size: file.size,
        addedAt: Date.now(), wordCount: words.length, text,
      })
      await refreshLibrary()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The document could not be imported.')
    } finally {
      setIsLoading(false)
    }
  }

  const removeDocument = async (event: React.MouseEvent, document: StoredDocument) => {
    event.stopPropagation()
    if (!window.confirm(`Remove “${document.name}” from this browser?`)) return
    await deleteDocument(document.id)
    localStorage.removeItem(bookmarkKey(document.id))
    await refreshLibrary()
  }

  const openDocument = (document: StoredDocument) => {
    setCurrentDocument(document)
    setView('VIEWER')
    window.scrollTo({ top: 0 })
  }

  const currentWords = currentDocument ? wordsFrom(currentDocument.text) : []
  const bookmark = currentDocument ? Number.parseInt(localStorage.getItem(bookmarkKey(currentDocument.id)) ?? '-1', 10) : -1

  return (
    <main className="app-container">
      {!isReaderActive && view === 'LIBRARY' && <>
        <header className="app-header">
          <div className="brand-mark"><BookOpen size={26} /></div>
          <div><h1>Fast Reader</h1><p>Read at your pace. Your files stay on this device.</p></div>
        </header>

        <label className={`glass-panel upload-dropzone ${isDragging ? 'drag-active' : ''} ${isLoading ? 'loading' : ''}`}
          onDragEnter={() => setIsDragging(true)} onDragLeave={() => setIsDragging(false)} onDrop={() => setIsDragging(false)}>
          <Upload size={42} aria-hidden="true" />
          <h2>{isLoading ? 'Working locally…' : 'Choose or drop a document'}</h2>
          <p>PDF, TXT, DOCX, or EPUB · processed entirely in your browser</p>
          <span className="btn btn-primary">Choose file</span>
          <input type="file" accept={SUPPORTED_EXTENSIONS} disabled={isLoading}
            onChange={(event) => { void importFile(event.target.files?.[0]); event.target.value = '' }} />
        </label>
        {error && <div className="error-message" role="alert">{error}</div>}
        <div className="privacy-note"><ShieldCheck size={17} /> No account, upload, backend, or network connection required.</div>

        <section className="library-section">
          <div className="section-heading"><h2>Your library</h2><span>{documents.length} {documents.length === 1 ? 'document' : 'documents'}</span></div>
          {!isLoading && documents.length === 0 ? <div className="empty-state">Your local library is empty. Add a document to begin.</div> :
            <div className="file-grid">{documents.map((document) =>
              <article key={document.id} className="glass-panel file-card" onClick={() => openDocument(document)}>
                <div className="file-icon"><FileText size={24} /></div>
                <div className="file-info"><h3 title={document.name}>{document.name}</h3>
                  <div className="meta">{document.wordCount.toLocaleString()} words
                    {localStorage.getItem(bookmarkKey(document.id)) && <span><Clock size={12} /> In progress</span>}
                  </div>
                </div>
                <button className="icon-button" onClick={(event) => void removeDocument(event, document)}
                  title="Remove document" aria-label={`Remove ${document.name}`}><Trash2 size={19} /></button>
              </article>)}</div>}
        </section>
      </>}

      {!isReaderActive && view === 'VIEWER' && currentDocument && <section>
        <div className="document-view-header">
          <button className="btn" onClick={() => setView('LIBRARY')}><ChevronLeft size={16} /> Library</button>
          <div><h2>{currentDocument.name}</h2><p>{currentWords.length.toLocaleString()} words · select any word to start</p></div>
        </div>
        <div className="text-container">{currentWords.map((word, index) =>
          <button type="button" key={`${index}-${word}`} className={`clickable-word ${bookmark === index ? 'bookmarked' : ''}`}
            onClick={() => { setReaderIndex(index); setIsReaderActive(true) }}>{word}</button>)}</div>
      </section>}

      {isReaderActive && currentDocument && <FastReader words={currentWords} startIndex={readerIndex}
        documentId={currentDocument.id} onClose={() => setIsReaderActive(false)} />}
    </main>
  )
}

function FastReader({ words, startIndex, documentId, onClose }: { words: string[], startIndex: number, documentId: string, onClose: () => void }) {
  const [index, setIndex] = useState(startIndex)
  const [isPlaying, setIsPlaying] = useState(false)
  const [wpm, setWpm] = useState(250)
  const [fontSize, setFontSize] = useState(64)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => localStorage.setItem(bookmarkKey(documentId), index.toString()), [index, documentId])
  const advanceWord = useCallback(() => setIndex((previous) => {
    if (previous >= words.length - 1) { setIsPlaying(false); return previous }
    return previous + 1
  }), [words.length])

  useEffect(() => {
    if (isPlaying) timerRef.current = setInterval(advanceWord, 60_000 / wpm)
    else if (timerRef.current) clearInterval(timerRef.current)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [isPlaying, wpm, advanceWord])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.code === 'Space') { event.preventDefault(); setIsPlaying((playing) => !playing) }
      else if (event.code === 'ArrowRight') setIndex((value) => Math.min(words.length - 1, value + 1))
      else if (event.code === 'ArrowLeft') setIndex((value) => Math.max(0, value - 1))
      else if (event.code === 'ArrowUp') setWpm((value) => Math.min(1_000, value + 25))
      else if (event.code === 'ArrowDown') setWpm((value) => Math.max(50, value - 25))
      else if (event.key === '+' || event.key === '=') setFontSize((value) => Math.min(120, value + 4))
      else if (event.key === '-' || event.key === '_') setFontSize((value) => Math.max(20, value - 4))
      else if (event.code === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [words.length, onClose])

  const currentWord = words[index] ?? ''
  const pivot = Math.max(0, Math.floor((currentWord.length - 1) / 2))
  return <div className="fullscreen-reader">
    <div className="progress-bar" style={{ width: `${((index + 1) / words.length) * 100}%` }} />
    <button className="close-reader" onClick={onClose} title="Close reader (Esc)" aria-label="Close reader"><X size={24} /></button>
    <div className="reader-word-container" style={{ fontSize }} aria-live="polite">
      <span>{currentWord.slice(0, pivot)}</span><strong>{currentWord.slice(pivot, pivot + 1)}</strong><span>{currentWord.slice(pivot + 1)}</span>
    </div>
    <div className="reader-controls">
      <button onClick={() => setWpm((value) => Math.min(1_000, value + 25))}><strong>{wpm}</strong><span>WPM</span><kbd>↑ / ↓</kbd></button>
      <button onClick={() => setFontSize((value) => Math.min(120, value + 4))}><strong>{fontSize}px</strong><span>Size</span><kbd>+ / −</kbd></button>
      <button onClick={() => setIsPlaying((playing) => !playing)}>{isPlaying ? <Pause size={18} /> : <Play size={18} />}<span>{isPlaying ? 'Pause' : 'Play'}</span><kbd>Space</kbd></button>
      <button onClick={() => setIndex((value) => Math.min(words.length - 1, value + 1))}><strong>{index + 1} / {words.length}</strong><span>Words</span><kbd>← / →</kbd></button>
    </div>
  </div>
}

export default App
