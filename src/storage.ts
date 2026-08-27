export type StoredDocument = {
  id: string
  name: string
  type: string
  size: number
  addedAt: number
  wordCount: number
  text: string
}

const DATABASE_NAME = 'fast-reader'
const STORE_NAME = 'documents'

const openDatabase = () => new Promise<IDBDatabase>((resolve, reject) => {
  const request = indexedDB.open(DATABASE_NAME, 1)
  request.onerror = () => reject(request.error)
  request.onsuccess = () => resolve(request.result)
  request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME, { keyPath: 'id' })
})

const runRequest = async <T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>) => {
  const database = await openDatabase()
  return new Promise<T>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode)
    const request = action(transaction.objectStore(STORE_NAME))
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
    transaction.oncomplete = () => database.close()
    transaction.onerror = () => reject(transaction.error)
  })
}

export const getDocuments = async () => {
  const documents = await runRequest<StoredDocument[]>('readonly', (store) => store.getAll())
  return documents.sort((first, second) => second.addedAt - first.addedAt)
}

export const saveDocument = (document: StoredDocument) =>
  runRequest<IDBValidKey>('readwrite', (store) => store.put(document))

export const deleteDocument = (id: string) =>
  runRequest<undefined>('readwrite', (store) => store.delete(id))
