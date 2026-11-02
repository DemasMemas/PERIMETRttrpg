// Шкала применяется только к проверкам Внимательности на слух.
export function hearingModifier(value) {
    const level=Number(value)||0;
    return level>=70?-5:level>=50?-3:level>20?-1:0;
}
