import React from 'react';
import { Github, Linkedin, Twitter } from 'lucide-react';

const Footer = () => {
    return (
        <footer className="bg-neutral-950 border-t border-neutral-900 py-12">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-center gap-6">
                <div className="text-center md:text-left">
                    <h2 className="text-2xl font-bold font-['Space_Grotesk'] text-white">Portfolio.</h2>
                    <p className="text-gray-500 text-sm mt-2">© {new Date().getFullYear()} Punith Reddy. All rights reserved.</p>
                </div>

                <div className="flex items-center gap-6">
                    <a href="#" className="text-gray-400 hover:text-white transition-colors">
                        <Github size={20} />
                    </a>
                    <a href="#" className="text-gray-400 hover:text-white transition-colors">
                        <Linkedin size={20} />
                    </a>
                    <a href="#" className="text-gray-400 hover:text-white transition-colors">
                        <Twitter size={20} />
                    </a>
                </div>
            </div>
        </footer>
    );
};

export default Footer;
