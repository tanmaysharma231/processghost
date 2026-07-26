export const runtime = 'edge'
export const dynamic = 'force-dynamic'

export default function NotFound() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <div style={{ textAlign: 'center' }}>
        <h1 style={{ fontSize: '48px', fontWeight: 700, margin: 0 }}>404</h1>
        <p style={{ fontSize: '16px', color: '#666', marginTop: '8px' }}>Page not found</p>
      </div>
    </div>
  )
}
