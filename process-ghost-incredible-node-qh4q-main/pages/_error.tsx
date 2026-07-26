import type { NextPageContext } from 'next'

function Error({ statusCode }: { statusCode?: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <div style={{ textAlign: 'center' }}>
        <h1 style={{ fontSize: '48px', fontWeight: 700, margin: 0 }}>{statusCode || 'Error'}</h1>
        <p style={{ fontSize: '16px', color: '#666', marginTop: '8px' }}>
          {statusCode === 404 ? 'Page not found' : 'An error occurred'}
        </p>
      </div>
    </div>
  )
}

Error.getInitialProps = ({ res, err }: NextPageContext) => {
  const statusCode = res ? res.statusCode : err ? err.statusCode : 404
  return { statusCode }
}

export default Error
