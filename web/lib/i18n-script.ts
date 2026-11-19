// Shared by the server layout (inline <head> script) and the client language store. No React imports here.
export type Lang = "en" | "fa";

export const LANG_KEY = "dockra-lang";

/**
 * Inline script for <head>: picks the saved language, or Persian when the browser's preferred language is
 * Persian, and sets <html lang dir> before the first paint so right-to-left layout never flashes.
 */
export const LANG_SCRIPT = `(function(){var l="en";try{var s=localStorage.getItem("${LANG_KEY}");if(s==="fa"||s==="en")l=s;else{var p=(navigator.languages&&navigator.languages[0])||navigator.language||"";if(/^fa\\b/i.test(p))l="fa";}}catch(e){}var d=document.documentElement;d.lang=l;d.dir=l==="fa"?"rtl":"ltr";})();`;
