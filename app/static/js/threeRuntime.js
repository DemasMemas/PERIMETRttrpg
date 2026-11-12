import { ColorManagement } from '../vendor/three-0.162.0/build/three.module.min.js';

// В локальной сборке r162 compileAsync пропускает материалы, освобождённые при выходе со сцены.
// Сохранённые цвета сцен и палитры материалов были настроены в числах Three r128.
ColorManagement.enabled = false;

export * from '../vendor/three-0.162.0/build/three.module.min.js';
