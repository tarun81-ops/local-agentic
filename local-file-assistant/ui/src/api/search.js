const SEARCH_URL = 'http://127.0.0.1:8756/search';

export async function search(query) {
  const token = await window.overlay.getApiToken();
  const response = await fetch(`${SEARCH_URL}?q=${encodeURIComponent(query)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()).results;
}
