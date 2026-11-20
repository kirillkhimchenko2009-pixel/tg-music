/** Collapse repeated rows from the same provider without merging alternate versions. */
export function dedupeAlbumTracks(tracks) {
  const seen = new Set();
  return tracks.filter(track => {
    const externalId = track.external_id ?? track.id;
    if (!track.source || externalId == null) return true;
    const key = `${track.source}|${String(externalId)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
