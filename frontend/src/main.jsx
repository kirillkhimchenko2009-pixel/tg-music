/* Точка входа Vibe Music v2 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import { initTelegram } from './tg.js';
import './ui.css';

initTelegram();

createRoot(document.getElementById('root')).render(<App />);
