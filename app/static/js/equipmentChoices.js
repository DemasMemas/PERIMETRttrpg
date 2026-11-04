// Выбор внутри открытого листа переживает обновление боевого состояния.
const selectedChoices=new Map();

export function rememberEquipmentChoice(select,key) {
    const saved=selectedChoices.get(key);
    if(saved!=null && Array.from(select.options).some(option=>option.value===saved))select.value=saved;
    select.addEventListener('change',()=>selectedChoices.set(key,select.value));
}
