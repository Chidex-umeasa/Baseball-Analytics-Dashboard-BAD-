import { useEffect, useState } from "react";
import { fetchSeasons } from "../utils/api";

interface SeasonSelectorProps {
  value: number | undefined;
  onChange: (season: number | undefined) => void;
}

export default function SeasonSelector({ value, onChange }: SeasonSelectorProps) {
  const [seasons, setSeasons] = useState<number[]>([]);

  useEffect(() => {
    fetchSeasons().then(setSeasons).catch(() => setSeasons([]));
  }, []);

  if (seasons.length === 0) return null;

  return (
    <div className="flex items-center gap-2">
      <label className="text-xs font-medium text-gray-500 dark:text-slate-400 whitespace-nowrap">
        Season
      </label>
      <select
        className="input-field text-sm"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
      >
        <option value="">All Seasons</option>
        {seasons.map((y) => (
          <option key={y} value={y}>
            {y}
          </option>
        ))}
      </select>
    </div>
  );
}
