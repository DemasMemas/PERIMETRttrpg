// Одно подтверждение удаления данных для своего и подключённого КПК.
export function createPdaWipeControl({disabled=false,hasProfiles=false,onWipe}){
    const root=document.createElement('section');root.className='pda-file-card';
    const button=(text,action)=>{const b=document.createElement('button');b.type='button';b.className='btn btn-secondary';b.textContent=text;b.disabled=disabled;b.onclick=action;return b;};
    const closed=()=>{root.replaceChildren(button('Чистая запись · очистить КПК',()=>{
        const warning=document.createElement('p');warning.textContent=`Книги, настройки, привязки к зарядам и ${hasProfiles?'аккаунты обоих профилей':'аккаунт'} будут удалены. Заметки, контакты и переписка исчезнут; вход на других КПК будет прекращён. Сам КПК, его название, заряд и установленные модификации сохранятся.`;
        const confirm=button('Удалить данные · 3 ОД',onWipe);confirm.dataset.pdaWipe='';confirm.className='btn btn-danger';
        root.replaceChildren(warning,confirm,button('Отмена',closed));
    }));};
    closed();return root;
}
