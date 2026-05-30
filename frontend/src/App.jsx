import React from 'react'
import { Routes, Route, Link } from 'react-router-dom'
import { Container, Navbar, Nav } from 'react-bootstrap'
import SessionsList from './pages/SessionsList'
import SessionDetail from './pages/SessionDetail'
import DataDictionary from './pages/DataDictionary'

function Layout() {
  return (
    <>
      <Navbar bg="dark" variant="dark" expand="lg" className="mb-4">
        <Container>
          <Navbar.Brand as={Link} to="/">Forza Horizon 6 Telemetry</Navbar.Brand>
          <Nav className="me-auto">
            <Nav.Link as={Link} to="/">Sessions</Nav.Link>
            <Nav.Link as={Link} to="/data-fields">Data Fields</Nav.Link>
          </Nav>
        </Container>
      </Navbar>

      <Container>
        <Routes>
          <Route path="/" element={<SessionsList />} />
          <Route path="/session/:sessionId" element={<SessionDetail />} />
          <Route path="/data-fields" element={<DataDictionary />} />
        </Routes>
      </Container>
    </>
  )
}

export default function App() {
  return <Layout />
}