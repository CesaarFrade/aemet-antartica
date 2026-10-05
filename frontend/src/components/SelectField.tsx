// frontend/src/components/SelectField.tsx
import type { ReactNode } from 'react';

/**
 * Styling of the filter controls, in one place so that adding a filter cannot make
 * the toolbar drift by copy-pasting a slightly different class string.
 */
export const CONTROL_CLASS =
  'w-full px-3 py-2 border border-slate-300 rounded-md bg-white focus:ring-2 focus:ring-blue-500 disabled:bg-slate-100 disabled:text-slate-400';

interface SelectFieldProps {
  /** Tied to the label, so the control is reachable by its visible name. */
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** The `<option>` / `<optgroup>` elements describing the choices. */
  children: ReactNode;
  disabled?: boolean;
  required?: boolean;
}

/**
 * Labelled `<select>` matching the layout of the rest of the filter form.
 *
 * The options are passed as children rather than as an array because the station
 * picker has to swap its whole body for a placeholder while the list loads, and the
 * location picker needs `<optgroup>`s; both stay declarative this way.
 */
export default function SelectField({
  id,
  label,
  value,
  onChange,
  children,
  disabled,
  required,
}: SelectFieldProps) {
  return (
    <div className="flex flex-col space-y-1">
      <label htmlFor={id} className="text-sm font-medium text-slate-600">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        required={required}
        className={CONTROL_CLASS}
      >
        {children}
      </select>
    </div>
  );
}
