/* Секция, которая мягко проявляется при первом попадании в вьюпорт.
   После срабатывания observer отключается — повторных анимаций нет. */
import React from 'react';
import { useSectionReveal } from '../hooks/useSectionReveal.js';

export default function Reveal({ as = 'section', className = '', children, ...rest }) {
  const { ref } = useSectionReveal();
  const C = as;
  return (
    <C ref={ref} className={'reveal ' + className} {...rest}>
      {children}
    </C>
  );
}
