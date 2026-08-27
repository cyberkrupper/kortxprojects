export const SUPPORTED_EXTENSIONS = '.pdf,.txt,.docx,.epub'

const extensionOf = (name: string) => name.toLowerCase().slice(name.lastIndexOf('.'))

const parsePdf = async (buffer: ArrayBuffer) => {
  const [pdfjs, { default: pdfWorker }] = await Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ])
  pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker
  const pdf = await pdfjs.getDocument({ data: new Uint8Array(buffer) }).promise
  const pages: string[] = []
  for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
    const page = await pdf.getPage(pageNumber)
    const content = await page.getTextContent()
    pages.push(content.items.map((item) => 'str' in item ? item.str : '').join(' '))
  }
  return pages.join('\n')
}

const parseEpub = async (buffer: ArrayBuffer) => {
  const { default: JSZip } = await import('jszip')
  const archive = await JSZip.loadAsync(buffer)
  const htmlFiles = Object.values(archive.files)
    .filter((entry) => !entry.dir && /\.(x?html?|htm)$/iu.test(entry.name))
    .sort((first, second) => first.name.localeCompare(second.name))

  const sections = await Promise.all(htmlFiles.map(async (entry) => {
    const html = await entry.async('string')
    const document = new DOMParser().parseFromString(html, 'text/html')
    document.querySelectorAll('script, style, nav').forEach((element) => element.remove())
    return document.body?.textContent?.replace(/\s+/gu, ' ').trim() ?? ''
  }))
  return sections.filter(Boolean).join('\n')
}

export const parseDocument = async (file: File) => {
  const extension = extensionOf(file.name)
  if (!SUPPORTED_EXTENSIONS.split(',').includes(extension)) {
    throw new Error('Unsupported file type. Choose a PDF, TXT, DOCX, or EPUB file.')
  }
  if (extension === '.txt') return file.text()

  const buffer = await file.arrayBuffer()
  if (extension === '.pdf') return parsePdf(buffer)
  if (extension === '.docx') {
    const { default: mammoth } = await import('mammoth')
    const result = await mammoth.extractRawText({ arrayBuffer: buffer })
    return result.value
  }
  return parseEpub(buffer)
}
