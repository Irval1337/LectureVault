import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/inter'
import '@fontsource-variable/source-serif-4'
import 'katex/dist/katex.min.css'
import './quartz/styles.scss'
import './design/tokens.css'
import './styles.css'
import App from './App'
import { SessionGate } from './components/session'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><BrowserRouter><SessionGate><App /></SessionGate></BrowserRouter></React.StrictMode>,
)
