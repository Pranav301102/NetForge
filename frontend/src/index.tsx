import React from 'react';
import ReactDOM from 'react-dom/client';
import { CopilotKit } from '@copilotkit/react-core';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);
root.render(
  <React.StrictMode>
    <CopilotKit publicApiKey="dummy_key_for_hackathon" runtimeUrl="http://localhost:8000/copilotkit" agent="default">
      <App />
    </CopilotKit>
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
