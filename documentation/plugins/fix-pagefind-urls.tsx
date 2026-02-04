import type { ZudokuPlugin } from "zudoku";

/**
 * Plugin to fix pagefind search result URLs by removing .html extensions.
 * Pagefind generates URLs with .html extensions (e.g., /query-guide.html#filtering),
 * but the site uses clean URLs without extensions (e.g., /query-guide#filtering).
 * 
 * This plugin intercepts pagefind UI initialization and adds a processResult
 * function to modify URLs before they're displayed.
 */
const fixPagefindUrlsPlugin: ZudokuPlugin = {
  initialize: async () => {
    // No async initialization needed
  },

  getHead: () => {
    return (
      <script
        key="zudoku-fix-pagefind-urls"
        id="zudoku-fix-pagefind-urls"
      >
        {`
            (function() {
              // Wait for pagefind UI to be initialized
              function fixPagefindUrls() {
                // Check if PagefindUI is available
                if (typeof window !== 'undefined' && window.PagefindUI) {
                  // Intercept PagefindUI constructor
                  const OriginalPagefindUI = window.PagefindUI;
                  
                  window.PagefindUI = function(options) {
                    // Get existing processResult if any
                    const existingProcessResult = options?.processResult;
                    
                    // Create new processResult that removes .html extensions
                    options = {
                      ...options,
                      processResult: function(result) {
                        // First apply existing processResult if it exists
                        if (existingProcessResult) {
                          result = existingProcessResult.call(this, result);
                          if (!result) return result;
                        }
                        
                        // Remove .html extension from URL
                        if (result && result.url && typeof result.url === 'string') {
                          result.url = result.url.replace(/\\.html(?=[#?]|$)/, '');
                        }
                        
                        // Also handle sub_results if they exist
                        if (result && result.sub_results && Array.isArray(result.sub_results)) {
                          result.sub_results = result.sub_results.map(function(subResult) {
                            if (subResult && subResult.url && typeof subResult.url === 'string') {
                              subResult.url = subResult.url.replace(/\\.html(?=[#?]|$)/, '');
                            }
                            return subResult;
                          });
                        }
                        
                        return result;
                      }
                    };
                    
                    // Call original constructor with modified options
                    return new OriginalPagefindUI(options);
                  };
                  
                  // Copy static properties if any
                  Object.setPrototypeOf(window.PagefindUI, OriginalPagefindUI);
                  Object.keys(OriginalPagefindUI).forEach(function(key) {
                    window.PagefindUI[key] = OriginalPagefindUI[key];
                  });
                }
              }
              
              // Try immediately and also on DOMContentLoaded
              if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', fixPagefindUrls);
              } else {
                fixPagefindUrls();
              }
              
              // Also try after a short delay in case pagefind loads later
              setTimeout(fixPagefindUrls, 100);
              setTimeout(fixPagefindUrls, 500);
              setTimeout(fixPagefindUrls, 1000);
            })();
        `}
      </script>
    );
  },
};

export default fixPagefindUrlsPlugin;
