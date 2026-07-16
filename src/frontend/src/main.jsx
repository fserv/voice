import React from 'react';
import ReactDOM from 'react-dom/client';
import { VocalBridgeProvider } from '@vocalbridgeai/react';
import App from './App.jsx';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <VocalBridgeProvider options={{ auth: { tokenUrl: '/api/voice-token' }, participantName: 'Web User' }}>
      <App />
    </VocalBridgeProvider>
  </React.StrictMode>
);
