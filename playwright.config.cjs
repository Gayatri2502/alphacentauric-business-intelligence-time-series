const {defineConfig}=require('@playwright/test');
module.exports=defineConfig({testDir:'tests/e2e',timeout:120000,workers:1,use:{baseURL:process.env.BASE_URL||'http://127.0.0.1:8015',headless:true,acceptDownloads:true,viewport:{width:1440,height:1000}},reporter:'list',outputDir:'playwright-results'});
