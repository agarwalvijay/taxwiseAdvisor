'use client'
import { useEffect, useState } from 'react'
import { useUser } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import Header from '@/components/Header'
import { clientsApi } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'

interface ClientRow {
  client_id: string
  name: string
  document_count: number
  plan_status: string
  created_at: string
}

function planStatusBadge(status: string) {
  if (!status || status === 'no_plan') return <Badge variant="secondary">No Plan</Badge>
  if (status === 'complete') return <Badge className="bg-[#1F4E79] text-white">Plan Complete</Badge>
  if (status === 'failed') return <Badge variant="destructive">Plan Failed</Badge>
  if (status.includes('running') || status.includes('generating') || status === 'queued')
    return <Badge className="bg-blue-500 text-white">Generating…</Badge>
  return <Badge className="bg-amber-500 text-white">In Progress</Badge>
}

export default function ClientsPage() {
  const { user, isLoaded } = useUser()
  const router = useRouter()
  const [clients, setClients] = useState<ClientRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [newClientName, setNewClientName] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    if (!isLoaded || !user) return
    setLoading(true)
    clientsApi
      .list(user.id)
      .then((data) => setClients(data))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load clients'))
      .finally(() => setLoading(false))
  }, [isLoaded, user])

  async function handleAddClient() {
    if (!user || !newClientName.trim()) return
    setAdding(true)
    try {
      await clientsApi.create(
        newClientName.trim(),
        user.id,
        user.fullName ?? 'Advisor',
        user.emailAddresses[0]?.emailAddress
      )
      setShowAddModal(false)
      setNewClientName('')
      // Refresh
      const updated = await clientsApi.list(user.id)
      setClients(updated)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to add client')
    } finally {
      setAdding(false)
    }
  }

  if (!isLoaded) return null

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Clients</h1>
          <Button
            onClick={() => setShowAddModal(true)}
            className="bg-[#1F4E79] hover:bg-[#1a4068] text-white"
          >
            + Add Client
          </Button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 mb-4 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="bg-white rounded-lg h-14 animate-pulse" />
            ))}
          </div>
        ) : clients.length === 0 ? (
          <div className="text-center py-16 text-gray-500">
            <p className="text-lg mb-2">No clients yet</p>
            <p className="text-sm">Click &quot;Add Client&quot; to get started</p>
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-[#1F4E79] text-white">
                  <th className="px-4 py-3 text-left">Client Name</th>
                  <th className="px-4 py-3 text-left">Documents</th>
                  <th className="px-4 py-3 text-left">Plan Status</th>
                  <th className="px-4 py-3 text-left">Added</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {clients.map((client, i) => (
                  <tr
                    key={client.client_id}
                    className={`cursor-pointer hover:bg-blue-50 transition-colors ${
                      i % 2 === 0 ? 'bg-white' : 'bg-slate-50'
                    }`}
                    onClick={() => router.push(`/clients/${client.client_id}`)}
                  >
                    <td className="px-4 py-3 font-medium text-gray-900">{client.name}</td>
                    <td className="px-4 py-3 text-gray-600">{client.document_count}</td>
                    <td className="px-4 py-3">{planStatusBadge(client.plan_status)}</td>
                    <td className="px-4 py-3 text-gray-500">
                      {client.created_at
                        ? new Date(client.created_at).toLocaleDateString()
                        : '—'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button className="text-[#1F4E79] hover:underline text-sm">View →</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>

      <Dialog open={showAddModal} onOpenChange={setShowAddModal}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add New Client</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <label className="text-sm font-medium text-gray-700 block mb-1">Client Name</label>
            <Input
              placeholder="e.g. Jane & John Smith"
              value={newClientName}
              onChange={(e) => setNewClientName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAddClient()}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddModal(false)}>Cancel</Button>
            <Button
              onClick={handleAddClient}
              disabled={adding || !newClientName.trim()}
              className="bg-[#1F4E79] hover:bg-[#1a4068] text-white"
            >
              {adding ? 'Adding...' : 'Add Client'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
