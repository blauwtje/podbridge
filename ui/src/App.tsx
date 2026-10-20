import { useSnapshot } from '@/store/snapshot'

export default function App() {
  const snapshot = useSnapshot()
  return (
    <main className="min-h-screen p-4">
      <h1 className="text-lg font-semibold">podbridge</h1>
      <p className="text-muted text-sm">{snapshot.steps.length} steps</p>
    </main>
  )
}
